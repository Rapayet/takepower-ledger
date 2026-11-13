#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mirror_sync.py - copia el libro publico de takepower.world a este repositorio (el espejo) y lo sella con OpenTimestamps.

Corre en GitHub Actions cada hora (ver .github/workflows/espejo.yml). No tiene ninguna credencial del servidor: solo LEE
https://takepower.world/ledger/ y escribe en la carpeta del espejo. Lo que hace, en orden:

  1. Baja el libro publicado y lo verifica con verify_ledger.py. Si no pasa la verificacion, no toca nada.
  2. Compara con lo que el espejo ya tiene: un bloque ya copiado NUNCA se sobreescribe. Si el servidor cambia un bloque que
     ya habia publicado, o tiene menos bloques que el espejo, el programa se detiene con un error (la reescritura queda a la vista).
  3. Copia los bloques nuevos, mas indice.json y estado.json, y comprueba que el espejo resultante tambien pasa la verificacion.
  4. Sella cada bloque nuevo con OpenTimestamps (ots stamp) y completa las pruebas pendientes (ots upgrade) cuando Bitcoin ya las incluyo.

Codigos de salida: 0 todo bien | 1 el libro NO pasa la verificacion, o reescribio / trunco un bloque ya copiado (no se toca nada)
                   | 2 no se pudo descargar el libro | 4 se copio todo pero fallo el sellado (la proxima corrida lo reintenta)
                   | 5 se copio y se sello todo, pero el libro publicado trae una ALERTA (cuentas nuevas que no cuadran, libro sin actualizar
                     hace mas de 3 horas, o alerta del generador): la corrida termina en rojo a proposito para que GitHub avise.
                   Si hay mas de uno, manda el de mayor gravedad: 1 > 5 > 4 > 0 (2 corta antes de empezar).
Uso: python3 mirror_sync.py [--url URL] [--destino CARPETA] [--ots RUTA | --sin-sellos] [--mensaje ARCHIVO]
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))     # verify_ledger.py esta al lado, aun con python -I
import verify_ledger as V  # noqa: E402

URL_POR_DEFECTO = 'https://takepower.world/ledger/'
MAGIC_OTS = bytes.fromhex('004f70656e54696d657374616d7073000050726f6f6600bf89e2e884e89294')   # cabecera de todo archivo .ots
ATESTACION_BITCOIN = bytes.fromhex('0588960d73d71901')                                      # una prueba que la contiene ya esta anclada en Bitcoin
ESPERA_OTS_S = 180
SELLOS_POR_LLAMADA = 50
MAX_PRUEBAS_POR_CORRIDA = 60


AVISOS_QUE_ALERTAN = ('W01', 'W03', 'W05')    # del verificador: cuentas que no cuadran, libro sin actualizar, alerta del generador


def una_linea(texto):
    """Lo que viene del libro publicado no es de confianza. Dentro de GitHub Actions un salto de linea o un retorno de carro en un texto
    impreso dejaria escribir un comando propio (::error::...) en el registro de la corrida: todo texto del libro se imprime en UNA linea."""
    return re.sub(r'[\x00-\x1f\x7f]+', ' ', str(texto))[:300]


def limpiar(resultado):
    """Deja los textos de avisos y errores del verificador en una sola linea (ver una_linea)."""
    resultado.avisos = [(c, una_linea(t)) for c, t in resultado.avisos]
    resultado.errores = [(c, una_linea(t)) for c, t in resultado.errores]
    return resultado


def aviso_gh(texto):
    """Dentro de GitHub Actions, un aviso (amarillo) visible en la pagina de la corrida."""
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        print('::warning::' + una_linea(texto))


def error_gh(texto):
    """Dentro de GitHub Actions, un error (rojo) visible en la pagina de la corrida."""
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        print('::error::' + una_linea(texto))


def separar_avisos(avisos, ya_vistos):
    """Reparte los avisos del libro publicado en (alertas, comunes). Una alerta W01 que el espejo ya tenia (el mismo texto, del mismo
    bloque) no vuelve a alertar: el hueco contable quedo en la historia y avisar cada hora no le sirve a nadie."""
    alertas, comunes = [], []
    for c, t in avisos:
        if c in AVISOS_QUE_ALERTAN and not (c == 'W01' and (c, t) in ya_vistos):
            alertas.append((c, t))
        else:
            comunes.append((c, t))
    return alertas, comunes


def nombres_bloques(carpeta):
    try:
        return sorted(f for f in os.listdir(os.path.join(carpeta, 'bloques')) if V.RE_BLOQUE.match(f))
    except OSError:
        return []


def leer(ruta):
    with open(ruta, 'rb') as fh:
        return fh.read()


def escribir_atomico(ruta, datos):
    os.makedirs(os.path.dirname(ruta) or '.', exist_ok=True)
    tmp = ruta + '.tmp-%d' % os.getpid()
    with open(tmp, 'wb') as fh:
        fh.write(datos)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, ruta)


def es_prueba_ots(datos):
    return datos.startswith(MAGIC_OTS)


def prueba_completa(datos):
    """True si la prueba .ots ya trae la atestacion de un bloque de Bitcoin (no solo la promesa de un calendario)."""
    return es_prueba_ots(datos) and ATESTACION_BITCOIN in datos


def comparar(destino, remoto):
    """Devuelve (problemas, nuevos). problemas: textos; nuevos: nombres de bloques que el espejo todavia no tiene."""
    local, lejos = nombres_bloques(destino), nombres_bloques(remoto)
    problemas = []
    if len(lejos) < len(local):
        problemas.append('TRUNCAMIENTO: el servidor publica %d bloques y el espejo ya copio %d' % (len(lejos), len(local)))
    for f in local:
        if f not in lejos:
            continue
        if leer(os.path.join(destino, 'bloques', f)) != leer(os.path.join(remoto, 'bloques', f)):
            problemas.append('REESCRITURA: el bloque %s que el servidor publico ya no es el que el espejo copio' % f)
    return problemas, [f for f in lejos if f not in local]


def descargar_con_reintentos(url, carpeta, intentos, espera):
    ultimo = None
    for i in range(intentos):
        try:
            shutil.rmtree(carpeta, ignore_errors=True)
            os.makedirs(carpeta)
            V.descargar(url, carpeta)
            return None
        except Exception as e:                      # red, 5xx, direccion no permitida, archivo enorme...
            ultimo = str(e)[:200]
            if i + 1 < intentos:
                time.sleep(espera)
    return ultimo


def ejecutar_ots(ots, args):
    try:
        p = subprocess.run([ots] + args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=ESPERA_OTS_S, check=False)
        return p.returncode, p.stdout.decode('utf-8', 'replace')[-400:]
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)[:200]


def sellar(destino, ots):
    """Sella los bloques que no tienen prueba y completa las pendientes. Devuelve (sellados, completadas, fallos)."""
    sellados, completadas, fallos = [], [], []
    bloques = os.path.join(destino, 'bloques')
    sin_prueba = [f for f in nombres_bloques(destino) if not os.path.exists(os.path.join(bloques, f + '.ots'))]
    for i in range(0, len(sin_prueba), SELLOS_POR_LLAMADA):
        grupo = sin_prueba[i:i + SELLOS_POR_LLAMADA]
        rc, salida = ejecutar_ots(ots, ['stamp'] + [os.path.join(bloques, f) for f in grupo])
        for f in grupo:
            ruta = os.path.join(bloques, f + '.ots')
            if os.path.exists(ruta) and es_prueba_ots(leer(ruta)):
                sellados.append(f)
                continue
            try:
                os.unlink(ruta)                       # nada a medias
            except OSError:
                pass
            fallos.append('no se pudo sellar %s (ots stamp, codigo %s): %s' % (f, rc, salida.strip().replace('\n', ' ')[-160:]))
    pendientes = []
    for f in nombres_bloques(destino):
        ruta = os.path.join(bloques, f + '.ots')
        if not os.path.exists(ruta):
            continue
        datos = leer(ruta)
        if not es_prueba_ots(datos):
            fallos.append('%s no es un archivo .ots valido' % os.path.basename(ruta))
        elif not prueba_completa(datos):
            pendientes.append(ruta)
    for ruta in reversed(pendientes[-MAX_PRUEBAS_POR_CORRIDA:]):          # las mas nuevas primero
        antes = leer(ruta)
        try:
            os.unlink(ruta + '.bak')                  # ots upgrade se niega a seguir si ya existe
        except OSError:
            pass
        rc, salida = ejecutar_ots(ots, ['upgrade', ruta])
        despues = leer(ruta) if os.path.exists(ruta) else b''
        try:
            os.unlink(ruta + '.bak')                  # la version anterior ya esta en el historial de git
        except OSError:
            pass
        if not es_prueba_ots(despues):                # el comando dejo la prueba rota: se restituye la anterior
            escribir_atomico(ruta, antes)
            fallos.append('ots upgrade dejo roto %s: se restituyo la prueba anterior' % os.path.basename(ruta))
        elif prueba_completa(despues):
            completadas.append(os.path.basename(ruta))
        # si sigue pendiente (codigo 1, "not complete"), no es un fallo: Bitcoin todavia no la incluyo
    return sellados, completadas, fallos


def sincronizar(a):
    ahora = V.hora_a_ms(a.ahora) if a.ahora else None
    destino = os.path.abspath(a.destino)
    tmp = tempfile.mkdtemp(prefix='espejo-')
    try:
        remoto = os.path.join(tmp, 'remoto')
        err = descargar_con_reintentos(a.url, remoto, a.reintentos, a.espera)
        if err:
            print('ERROR no se pudo descargar el libro: %s' % err, file=sys.stderr)
            return 2

        r = limpiar(V.verificar(remoto, ahora))
        rl = limpiar(V.verificar(destino, ahora)) if nombres_bloques(destino) else None
        ya_vistos = set(x for x in rl.avisos if x[0] == 'W01') if rl is not None else set()
        alertas, comunes = separar_avisos(r.avisos, ya_vistos)
        for c, t in comunes:
            print('AVISO [%s] %s' % (c, t))
            aviso_gh('[%s] %s' % (c, t))
        for c, t in alertas:
            print('ALERTA [%s] %s' % (c, t))
            error_gh('ALERTA [%s] %s' % (c, t))
        if r.errores:
            for c, t in r.errores:
                print('ERROR [%s] %s' % (c, t))
            print('FALLO: el libro publicado NO pasa la verificacion. No se copia nada.', file=sys.stderr)
            return 1

        if rl is not None and rl.errores:
            for c, t in rl.errores:
                print('ERROR del espejo [%s] %s' % (c, t))
            print('FALLO: el espejo mismo no pasa la verificacion: hace falta una persona. No se copia nada.', file=sys.stderr)
            return 1
        problemas, nuevos = comparar(destino, remoto)
        if problemas:
            for t in problemas:
                print('ERROR ' + t)
            print('FALLO: el servidor cambio lo que ya habia publicado. No se copia nada.', file=sys.stderr)
            return 1

        for f in nuevos:
            escribir_atomico(os.path.join(destino, 'bloques', f), leer(os.path.join(remoto, 'bloques', f)))
        for f in ('indice.json', 'estado.json'):
            if os.path.exists(os.path.join(remoto, f)):
                escribir_atomico(os.path.join(destino, f), leer(os.path.join(remoto, f)))

        rf = limpiar(V.verificar(destino, ahora))
        if rf.errores:
            for c, t in rf.errores:
                print('ERROR [%s] %s' % (c, t))
            print('FALLO: el espejo no pasa la verificacion despues de copiar. No hacer commit.', file=sys.stderr)
            return 1

        sellados, completadas, fallos = [], [], []
        if a.ots:
            sellados, completadas, fallos = sellar(destino, a.ots)
        for t in fallos:
            print('AVISO ' + t)
            aviso_gh(t)

        u = rf.ultimo
        print('OK espejo al dia: %d bloques, %d movimientos; bloques nuevos %d; sellados %d; pruebas completadas %d; ultimo bloque %d (%s) %s%s'
              % (rf.bloques, rf.movimientos, len(nuevos), len(sellados), len(completadas), u['bloque'], u['motivo'], u['sha256'],
                 ' (con %d alerta(s) del libro: la corrida termina en rojo a proposito para que GitHub avise)' % len(alertas) if alertas else ''))
        if a.mensaje:
            linea = 'espejo: %d bloques, ultimo %d (%s) %s' % (rf.bloques, u['bloque'], u['motivo'], u['sha256'][:12])
            if nuevos:
                linea = 'espejo: +%d bloque(s) hasta el %d (%s), %d movimientos, %s' % (len(nuevos), u['bloque'], u['motivo'], rf.movimientos, u['sha256'][:12])
            elif completadas:
                linea = 'espejo: %d prueba(s) de OpenTimestamps ya en Bitcoin' % len(completadas)
            escribir_atomico(a.mensaje, (linea + '\n').encode('ascii'))
        return 5 if alertas else (4 if fallos else 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Copia y sella el libro publico de takepower.world en este espejo')
    ap.add_argument('--url', default=URL_POR_DEFECTO)
    ap.add_argument('--destino', default='.')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--ots', default='ots', help='programa de OpenTimestamps (por defecto: ots)')
    g.add_argument('--sin-sellos', dest='ots', action='store_const', const=None, help='copiar sin sellar')
    ap.add_argument('--mensaje', help='archivo donde escribir el mensaje del commit')
    ap.add_argument('--reintentos', type=int, default=3)
    ap.add_argument('--espera', type=float, default=20.0, help='segundos entre reintentos de descarga')
    ap.add_argument('--ahora', help='hora de referencia (solo para pruebas)')
    a = ap.parse_args(argv)
    if a.ahora and V.hora_a_ms(a.ahora) is None:
        print('--ahora tiene que ser una hora como 2026-11-20T12:00:00.000Z', file=sys.stderr)
        return 2
    if a.reintentos < 1:
        print('--reintentos tiene que ser al menos 1', file=sys.stderr)
        return 2
    return sincronizar(a)


if __name__ == '__main__':
    sys.exit(main())
