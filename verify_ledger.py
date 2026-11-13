#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_ledger.py - verificador independiente del libro publico de takepower.world (formato takepower-ledger/1).

Esta escrito solo a partir de la especificacion (SPEC.md), con la biblioteca estandar de Python 3.8+, sin usar el codigo del generador.

Uso:
  python3 verify_ledger.py CARPETA            CARPETA contiene bloques/ (y, si existen, indice.json y estado.json)
  python3 verify_ledger.py --url https://takepower.world/ledger/
Opciones:
  --json            imprime un resumen en JSON
  --estricto        los avisos tambien hacen fallar (codigo 1)
  --edad-max H      horas sin actualizar el libro antes de avisar (por defecto 3)
  --ahora ISO       hora de referencia (solo para pruebas)
  --max-bloques N   con --url: error si el servidor tiene mas de N bloques (por defecto 100000)
Codigos de salida: 0 todo bien | 1 hay errores | 2 no se pudo leer / uso | 3 la cadena es correcta pero hay avisos.
Lo que NO puede comprobar: que el libro este completo respecto de lo que paso de verdad en el nodo, ni que un reclamo sea de una persona real.
"""
import calendar
import datetime
import hashlib
import json
import os
import re
import sys
import time

FORMATO = 'takepower-ledger/1'
CERO = '0' * 64
MOTIVOS = ('inicio', 'cinco_movimientos', 'sin_clasificar', '24_horas', 'latido')
TIPOS = ('entrada', 'reclamo', 'liquidez', 'gasto', 'retiro', 'sin_clasificar')
CAMPOS_BLOQUE = ('formato', 'bloque', 'nodo', 'anterior', 'cerrado_en', 'motivo', 'rango', 'saldo_sat', 'credito_comisiones_sat', 'movimientos')
CAMPOS_MOV = ('n', 'hora', 'tipo', 'sats', 'comision_msat', 'hash_pago', 'tx', 'ref')
MOVS_POR_BLOQUE = 5
HORAS_MAX = 24
LATIDO_DIAS = 7
GRACIA_MIN = 15
HORA_MS = 3600000
DIA_MS = 86400000
TOLERANCIA_RELOJ_MS = 10 * 60000
SEGURO = 2 ** 53 - 1
# Se usan siempre con .match (ancla al principio). \Z ancla al final SIN aceptar un salto de linea de mas (como si lo hace $); [0-9] y no \d, que acepta cifras de otros alfabetos.
RE_HEX64 = re.compile(r'[0-9a-f]{64}\Z')
RE_NODO = re.compile(r'[0-9a-f]{66}\Z')
RE_REF = re.compile(r'id:[0-9A-Za-z_-]{1,64}\Z')
RE_HORA = re.compile(r'([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})\.([0-9]{3})Z\Z')
RE_BLOQUE = re.compile(r'[0-9]{6}\.json\Z')
MAX_ARCHIVO = 5 * 1000 * 1000
MAX_BLOQUES_DESCARGA = 100000


class Resultado:
    def __init__(self):
        self.errores = []
        self.avisos = []
        self.bloques = 0
        self.movimientos = 0
        self.nodo = None
        self.ultimo = None
        self.saldo_sat = None

    def error(self, codigo, texto):
        self.errores.append((codigo, texto))

    def aviso(self, codigo, texto):
        self.avisos.append((codigo, texto))

    def resumen(self):
        return {
            'ok': not self.errores,
            'errores': ['%s %s' % e for e in self.errores],
            'avisos': ['%s %s' % a for a in self.avisos],
            'bloques': self.bloques,
            'movimientos': self.movimientos,
            'nodo': self.nodo,
            'ultimo_bloque': self.ultimo,
            'saldo_sat': self.saldo_sat,
        }


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def es_entero(x):
    return type(x) is int and -SEGURO <= x <= SEGURO


def es_cuenta(x):
    return es_entero(x) and x >= 0


def js(s):
    return json.dumps(s)


def hora_a_ms(s):
    """'2026-11-20T12:00:00.123Z' -> milisegundos desde 1970; None si no es una hora real y canonica."""
    if not isinstance(s, str):
        return None
    m = RE_HORA.match(s)
    if not m:
        return None
    y, mo, d, h, mi, se, ms = (int(g) for g in m.groups())
    try:
        dt = datetime.datetime(y, mo, d, h, mi, se)
    except ValueError:
        return None
    if y < 1970:
        return None
    return calendar.timegm(dt.timetuple()) * 1000 + ms


def igual(a, b):
    """Igualdad estricta de valores JSON: True no es 1, y 1.0 no es 1 (la de Python los mezcla)."""
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _sin_duplicados(pares):
    d = {}
    for k, v in pares:
        if k in d:
            raise ValueError('clave repetida: ' + k)
        d[k] = v
    return d


def _constante(c):
    raise ValueError('constante no permitida: ' + c)


def leer_archivo(ruta):
    """Bytes de un archivo, sin leer nunca mas de MAX_ARCHIVO (un enlace a /dev/zero no cuelga el programa ni llena la memoria). ValueError si pasa del tope."""
    with open(ruta, 'rb') as fh:
        datos = fh.read(MAX_ARCHIVO + 1)
    if len(datos) > MAX_ARCHIVO:
        raise ValueError('archivo demasiado grande')
    return datos


def leer_json(b):
    """Bytes -> objeto. Solo ASCII; sin claves repetidas; sin NaN/Infinity."""
    texto = b.decode('ascii')
    return json.loads(texto, object_pairs_hook=_sin_duplicados, parse_constant=_constante)


def canon_mov(m):
    p = ['"n":%d' % m['n'], '"hora":' + js(m['hora']), '"tipo":' + js(m['tipo']), '"sats":%d' % m['sats'],
         '"comision_msat":%d' % m['comision_msat'], '"hash_pago":' + ('null' if m['hash_pago'] is None else js(m['hash_pago']))]
    if 'tx' in m:
        p.append('"tx":' + js(m['tx']))
    if 'ref' in m:
        p.append('"ref":' + js(m['ref']))
    return '{' + ','.join(p) + '}'


def canon_bloque(b):
    movs = b['movimientos']
    rango = 'null' if b['rango'] is None else '[%d,%d]' % (b['rango'][0], b['rango'][1])
    lineas = [
        '  "formato": ' + js(b['formato']),
        '  "bloque": %d' % b['bloque'],
        '  "nodo": ' + js(b['nodo']),
        '  "anterior": ' + js(b['anterior']),
        '  "cerrado_en": ' + js(b['cerrado_en']),
        '  "motivo": ' + js(b['motivo']),
        '  "rango": ' + rango,
        '  "saldo_sat": %d' % b['saldo_sat'],
        '  "credito_comisiones_sat": %d' % b['credito_comisiones_sat'],
        '  "movimientos": [' + (('\n' + ',\n'.join('    ' + canon_mov(m) for m in movs) + '\n  ') if movs else '') + ']',
    ]
    return ('{\n' + ',\n'.join(lineas) + '\n}\n').encode('ascii')


def forma_movimiento(m):
    """None si el movimiento tiene la forma publica correcta; si no, el motivo."""
    if type(m) is not dict:
        return 'no es un objeto'
    extra = [k for k in m if k not in CAMPOS_MOV]
    if extra:
        return 'campo no permitido: ' + extra[0]
    for k in ('n', 'hora', 'tipo', 'sats', 'comision_msat', 'hash_pago'):
        if k not in m:
            return 'falta ' + k
    if not es_entero(m['n']) or m['n'] < 1:
        return 'n invalido'
    if hora_a_ms(m['hora']) is None:
        return 'hora invalida'
    if m['tipo'] not in TIPOS:
        return 'tipo desconocido'
    if not es_entero(m['sats']):
        return 'sats invalido'
    if not es_cuenta(m['comision_msat']):
        return 'comision invalida'
    if m['hash_pago'] is not None and not (isinstance(m['hash_pago'], str) and RE_HEX64.match(m['hash_pago'])):
        return 'hash_pago invalido'
    if 'tx' in m and not (isinstance(m['tx'], str) and RE_HEX64.match(m['tx'])):
        return 'tx invalido'
    if 'ref' in m and not (isinstance(m['ref'], str) and RE_REF.match(m['ref'])):
        return 'ref invalida'
    if m['hash_pago'] is None and 'tx' not in m and 'ref' not in m:
        return 'sin hash, txid ni referencia'
    if m['tipo'] == 'entrada':
        if m['sats'] < 0:
            return 'una entrada con sats negativos'
    elif m['tipo'] != 'sin_clasificar' and m['sats'] > 0:
        return 'una salida con sats positivos'
    return None


def clave_mov(m):
    """Identificador de un movimiento: su hash de pago; si no tiene, su txid; si no, su referencia. None si no tiene ninguno de los tres."""
    h = m.get('hash_pago')
    if isinstance(h, str) and h:
        return h
    t = m.get('tx')
    if isinstance(t, str) and t:
        return 'tx:' + t
    ref = m.get('ref')
    return ref if isinstance(ref, str) and ref else None


def _es_salida_sin_clasificar(m):
    return m['tipo'] == 'sin_clasificar' and m['sats'] < 0


def verificar_bloques(carpeta, r):
    """Lee y comprueba bloques/NNNNNN.json. Devuelve la lista de bloques validos (con su sha) leidos hasta el primer error grave."""
    dirb = os.path.join(carpeta, 'bloques')
    try:
        nombres = sorted(f for f in os.listdir(dirb) if RE_BLOQUE.match(f))
    except OSError:
        r.error('E01', 'no hay carpeta bloques/ en ' + carpeta)
        return []
    if not nombres:
        r.error('E01', 'no hay ningun bloque')
        return []
    bloques = []
    prev_sha, prev_cierre, nodo, esperado_n = CERO, None, None, 1
    vistos = {}
    for i, f in enumerate(nombres):
        num = i + 1
        if f != '%06d.json' % num:
            r.error('E02', 'falta el bloque %d (el siguiente archivo es %s)' % (num, f))
            break
        ruta = os.path.join(dirb, f)
        try:
            datos = leer_archivo(ruta)
            b = leer_json(datos)
        except (OSError, ValueError, UnicodeDecodeError, RecursionError) as e:
            r.error('E03', 'el bloque %d no es un JSON valido en ASCII (%s)' % (num, str(e)[:80]))
            break
        if type(b) is not dict or set(b.keys()) != set(CAMPOS_BLOQUE):
            r.error('E04', 'el bloque %d no tiene exactamente los campos del formato' % num)
            break
        if b['formato'] != FORMATO:
            r.error('E06', 'el bloque %d dice un formato desconocido' % num)
            break
        if not es_entero(b['bloque']) or b['bloque'] != num:
            r.error('E07', 'el bloque %d dice ser el numero %r' % (num, b['bloque']))
            break
        if not (isinstance(b['nodo'], str) and RE_NODO.match(b['nodo'])):
            r.error('E08', 'el bloque %d no trae un nodo valido' % num)
            break
        if nodo is not None and b['nodo'] != nodo:
            r.error('E08', 'el bloque %d dice otro nodo que el primero' % num)
            break
        nodo = b['nodo']
        if not (isinstance(b['anterior'], str) and RE_HEX64.match(b['anterior'])) or b['anterior'] != prev_sha:
            r.error('E09', 'la cadena se rompe en el bloque %d: "anterior" no es el hash del archivo del bloque %d' % (num, num - 1))
            break
        cierre = hora_a_ms(b['cerrado_en'])
        if cierre is None:
            r.error('E10', 'el bloque %d tiene una hora de cierre invalida' % num)
            break
        if prev_cierre is not None and cierre < prev_cierre:
            r.error('E10', 'el bloque %d se cerro antes que el anterior' % num)
            break
        if b['motivo'] not in MOTIVOS:
            r.error('E11', 'el bloque %d tiene un motivo desconocido' % num)
            break
        if (num == 1) != (b['motivo'] == 'inicio'):
            r.error('E11', 'el motivo "inicio" es solo del primer bloque, y el primer bloque tiene que ser "inicio" (bloque %d)' % num)
            break
        if not es_cuenta(b['saldo_sat']) or not es_cuenta(b['credito_comisiones_sat']):
            r.error('E04', 'el bloque %d tiene un saldo invalido' % num)
            break
        movs = b['movimientos']
        if type(movs) is not list:
            r.error('E04', 'el bloque %d no trae la lista de movimientos' % num)
            break
        malo = False
        for m in movs:
            motivo_forma = forma_movimiento(m)
            if motivo_forma:
                r.error('E12', 'el bloque %d trae un movimiento con forma invalida (%s)' % (num, motivo_forma))
                malo = True
                break
            if m['n'] != esperado_n:
                r.error('E13', 'el bloque %d: el movimiento %d debia ser el %d' % (num, m['n'], esperado_n))
                malo = True
                break
            esperado_n += 1
            k = clave_mov(m)
            if k in vistos:
                r.error('E15', 'el movimiento %d repite el identificador del movimiento %d' % (m['n'], vistos[k]))
                malo = True
                break
            vistos[k] = m['n']
        if malo:
            break
        rango = [movs[0]['n'], movs[-1]['n']] if movs else None
        if b['rango'] != rango or (rango is not None and not (type(b['rango']) is list and all(type(x) is int for x in b['rango']))):
            r.error('E14', 'el bloque %d: el rango no coincide con sus movimientos' % num)
            break
        if canon_bloque(b) != datos:
            r.error('E05', 'el bloque %d no esta en la forma canonica de la especificacion' % num)
            break
        reglas_bloque(b, cierre, prev_cierre, r)
        sha = sha256(datos)
        bloques.append({'num': num, 'sha': sha, 'data': b, 'cierre': cierre, 'bytes': datos})
        prev_sha, prev_cierre = sha, cierre
    return bloques


def reglas_bloque(b, cierre, prev_cierre, r):
    """Que el motivo del bloque sea coherente con las reglas publicadas (SPEC.md, seccion 4)."""
    num, motivo, movs = b['bloque'], b['motivo'], b['movimientos']
    horas = [hora_a_ms(m['hora']) for m in movs]
    for m, t in zip(movs, horas):
        if t > cierre + TOLERANCIA_RELOJ_MS:
            r.error('E17', 'el bloque %d trae el movimiento %d con una hora posterior al cierre del bloque' % (num, m['n']))
    if any(horas[i] > horas[i + 1] for i in range(len(horas) - 1)):
        r.error('E16', 'el bloque %d: los movimientos no estan en orden de hora' % num)
    n = len(movs)
    salidas_sc = [m for m in movs if _es_salida_sin_clasificar(m)]
    if motivo in ('inicio', 'latido') and n:
        r.error('E16', 'el bloque %d es "%s" y no deberia traer movimientos' % (num, motivo))
    if motivo == 'latido' and prev_cierre is not None and cierre - prev_cierre < LATIDO_DIAS * DIA_MS:
        r.error('E16', 'el bloque %d es un latido antes de los %d dias del bloque anterior' % (num, LATIDO_DIAS))
    if motivo == 'cinco_movimientos':
        if n != MOVS_POR_BLOQUE:
            r.error('E16', 'el bloque %d es "cinco_movimientos" pero trae %d' % (num, n))
        if salidas_sc:
            r.error('E16', 'el bloque %d es "cinco_movimientos" pero trae una salida sin clasificar (esa cierra el bloque)' % num)
    if motivo == '24_horas':
        if not 1 <= n < MOVS_POR_BLOQUE:
            r.error('E16', 'el bloque %d es "24_horas" y trae %d movimientos (tiene que traer de 1 a 4)' % (num, n))
        if salidas_sc:
            r.error('E16', 'el bloque %d es "24_horas" pero trae una salida sin clasificar (esa cierra el bloque)' % num)
        if n and cierre - horas[0] < HORAS_MAX * HORA_MS:
            r.error('E16', 'el bloque %d es "24_horas" pero su movimiento mas antiguo tenia menos de 24 horas' % num)
    if motivo == 'sin_clasificar':
        if not 1 <= n <= MOVS_POR_BLOQUE:
            r.error('E16', 'el bloque %d es "sin_clasificar" y trae %d movimientos (tiene que traer de 1 a 5)' % (num, n))
        elif movs[-1]['tipo'] != 'sin_clasificar':
            r.error('E16', 'el bloque %d es "sin_clasificar" pero su ultimo movimiento no lo es' % num)
        elif cierre - horas[-1] < GRACIA_MIN * 60000:
            r.error('E16', 'el bloque %d cerro una salida sin clasificar antes de los %d minutos de gracia' % (num, GRACIA_MIN))
        if any(_es_salida_sin_clasificar(m) for m in movs[:-1]):
            r.error('E16', 'el bloque %d trae mas de una salida sin clasificar (cada una cierra el suyo)' % num)
    # retrasos (no son errores: pueden ser una caida del servicio)
    margen = 2 * HORA_MS
    if motivo == '24_horas' and n and cierre - horas[0] > HORAS_MAX * HORA_MS + margen:
        r.aviso('W02', 'el bloque %d se cerro %d h despues de lo que marca la regla de 24 horas' % (num, (cierre - horas[0] - HORAS_MAX * HORA_MS) // HORA_MS))
    if motivo == 'cinco_movimientos' and n and cierre - horas[-1] > margen:
        r.aviso('W02', 'el bloque %d se cerro %d h despues de su quinto movimiento' % (num, (cierre - horas[-1]) // HORA_MS))
    if motivo == 'sin_clasificar' and n and cierre - horas[-1] > GRACIA_MIN * 60000 + margen:
        r.aviso('W02', 'el bloque %d se cerro %d h despues de terminar la gracia de una salida sin clasificar' % (num, (cierre - horas[-1]) // HORA_MS))
    if motivo == 'latido' and prev_cierre is not None and cierre - prev_cierre > LATIDO_DIAS * DIA_MS + margen:
        r.aviso('W02', 'el latido del bloque %d llego %d h tarde' % (num, (cierre - prev_cierre - LATIDO_DIAS * DIA_MS) // HORA_MS))


def cuentas(bloques, r):
    """Avisa si el saldo de un bloque no es el del bloque anterior mas lo que se movio (lo que no esta en el libro)."""
    for prev, b in zip(bloques, bloques[1:]):
        d = b['data']
        suma = sum(m['sats'] for m in d['movimientos'])
        hueco = d['saldo_sat'] - prev['data']['saldo_sat'] - suma
        tolerancia = max(5, 2 * len(d['movimientos']))
        if abs(hueco) > tolerancia:
            r.aviso('W01', 'las cuentas del bloque %d no cuadran: el saldo cambio %+d sats de mas o de menos respecto de sus movimientos (tolerancia %d)' % (b['num'], hueco, tolerancia))


def leer_opcional(ruta, r, nombre):
    try:
        datos = leer_archivo(ruta)
        return leer_json(datos)
    except FileNotFoundError:
        return None
    except OSError as e:
        r.aviso('W07', '%s no se pudo leer (%s)' % (nombre, str(e)[:60]))
        return None
    except (ValueError, UnicodeDecodeError, RecursionError):
        r.aviso('W07', '%s no es un JSON valido (se ignora)' % nombre)
        return None


def comprobar_indice(carpeta, bloques, r):
    idx = leer_opcional(os.path.join(carpeta, 'indice.json'), r, 'indice.json')
    if idx is None:
        return
    if type(idx) is not dict or idx.get('formato') != FORMATO or type(idx.get('bloques')) is not list:
        r.error('E18', 'indice.json no tiene la forma esperada')
        return
    entradas = idx['bloques']
    for i, e in enumerate(entradas):
        if i >= len(bloques):
            r.error('E18', 'indice.json habla del bloque %s, que no existe' % (e.get('bloque') if type(e) is dict else '?'))
            return
        b = bloques[i]
        esperado = {'bloque': b['num'], 'archivo': 'bloques/%06d.json' % b['num'], 'sha256': b['sha'], 'cerrado_en': b['data']['cerrado_en'],
                    'motivo': b['data']['motivo'], 'movimientos': len(b['data']['movimientos'])}
        if not igual(e, esperado):
            r.error('E18', 'la entrada %d de indice.json no coincide con el bloque %d' % (i + 1, i + 1))
            return
    if len(entradas) < len(bloques):
        r.aviso('W04', 'indice.json va atrasado: lista %d bloques y hay %d' % (len(entradas), len(bloques)))


def comprobar_estado(carpeta, bloques, r, ahora_ms, edad_max_h):
    est = leer_opcional(os.path.join(carpeta, 'estado.json'), r, 'estado.json')
    if est is None:
        return
    if type(est) is not dict or est.get('formato') != FORMATO:
        r.error('E19', 'estado.json no tiene la forma esperada')
        return
    if est.get('nodo') != bloques[0]['data']['nodo']:
        r.error('E19', 'estado.json dice otro nodo que los bloques')
    actualizado = hora_a_ms(est.get('actualizado_en'))
    if actualizado is None:
        r.error('E19', 'estado.json no trae una hora de actualizacion valida')
    elif ahora_ms - actualizado > edad_max_h * HORA_MS:
        r.aviso('W03', 'el libro no se actualiza desde hace %d horas (estado.json dice %s)' % ((ahora_ms - actualizado) // HORA_MS, est['actualizado_en']))
    elif actualizado - ahora_ms > TOLERANCIA_RELOJ_MS:
        r.aviso('W08', 'estado.json dice %s, una hora que todavia no llego (el reloj del servidor o el de esta maquina esta mal)' % est['actualizado_en'])
    if igual(est.get('bloques'), len(bloques)):
        ult = bloques[-1]
        esperado = {'bloque': ult['num'], 'sha256': ult['sha'], 'cerrado_en': ult['data']['cerrado_en'], 'motivo': ult['data']['motivo']}
        if not igual(est.get('ultimo_bloque'), esperado):
            r.error('E19', 'el ultimo bloque que dice estado.json no es el ultimo bloque')
        total = sum(len(b['data']['movimientos']) for b in bloques)
        if not igual(est.get('movimientos_sellados'), total):
            r.error('E19', 'estado.json dice %r movimientos sellados y los bloques traen %d' % (est.get('movimientos_sellados'), total))
    elif es_entero(est.get('bloques')) and est['bloques'] < len(bloques):
        r.aviso('W06', 'estado.json va atrasado: dice %d bloques y hay %d' % (est['bloques'], len(bloques)))
    else:
        r.error('E19', 'estado.json dice %r bloques y hay %d' % (est.get('bloques'), len(bloques)))
    sellados = set()
    for b in bloques:
        for m in b['data']['movimientos']:
            sellados.add(clave_mov(m))
    abierto = est.get('abierto')
    if type(abierto) is list:
        for m in abierto:
            if type(m) is dict and clave_mov(m) in sellados:
                r.error('E20', 'estado.json lista como abierto un movimiento que ya esta sellado en un bloque')
                break
    alertas = est.get('alertas')
    for a in alertas if type(alertas) is list else []:
        r.aviso('W05', 'alerta del generador: %s' % str(a)[:200])


def verificar(carpeta, ahora_ms=None, edad_max_h=3):
    r = Resultado()
    if ahora_ms is None:
        ahora_ms = int(time.time() * 1000)
    bloques = verificar_bloques(carpeta, r)
    r.bloques = len(bloques)
    if bloques:
        r.nodo = bloques[0]['data']['nodo']
        ult = bloques[-1]
        r.ultimo = {'bloque': ult['num'], 'sha256': ult['sha'], 'cerrado_en': ult['data']['cerrado_en'], 'motivo': ult['data']['motivo']}
        r.saldo_sat = ult['data']['saldo_sat']
        r.movimientos = sum(len(b['data']['movimientos']) for b in bloques)
        if not r.errores:
            cuentas(bloques, r)
            if ult['cierre'] - ahora_ms > TOLERANCIA_RELOJ_MS:
                r.aviso('W08', 'el ultimo bloque dice haberse cerrado en %s, una hora que todavia no llego (el reloj del servidor o el de esta maquina esta mal)' % ult['data']['cerrado_en'])
            comprobar_indice(carpeta, bloques, r)
            comprobar_estado(carpeta, bloques, r, ahora_ms, edad_max_h)
    return r


def direccion_permitida(url):
    """https siempre; http solo hacia la propia maquina (para pruebas). Nada de ftp:// ni file://."""
    import urllib.parse
    u = urllib.parse.urlparse(url)
    return u.scheme == 'https' or (u.scheme == 'http' and u.hostname in ('localhost', '127.0.0.1', '::1'))


def descargar(url_base, destino, max_bloques=MAX_BLOQUES_DESCARGA):
    """Baja indice.json, estado.json y bloques/NNNNNN.json a la carpeta destino (hasta que el servidor diga 404).

    Si lo que llega como bloque no empieza con "{" (por ejemplo una pagina de error con codigo 200), se guarda igual,
    para que la verificacion lo senale, y no se piden mas bloques. Si hay mas de max_bloques bloques, es un error."""
    import urllib.request
    import urllib.error
    base = url_base.rstrip('/') + '/'
    if not direccion_permitida(base):
        raise ValueError('la direccion tiene que empezar por https://')

    class SoloDireccionesPermitidas(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not direccion_permitida(newurl):
                raise urllib.error.HTTPError(newurl, code, 'redireccion no permitida', headers, fp)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    abridor = urllib.request.build_opener(SoloDireccionesPermitidas)

    def traer(ruta):
        req = urllib.request.Request(base + ruta, headers={'User-Agent': 'takepower-ledger-verifier/1'})
        try:
            with abridor.open(req, timeout=20) as resp:
                datos = resp.read(MAX_ARCHIVO + 1)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
        if len(datos) > MAX_ARCHIVO:
            raise ValueError(ruta + ' es demasiado grande')
        return datos

    os.makedirs(os.path.join(destino, 'bloques'), exist_ok=True)
    for nombre in ('indice.json', 'estado.json'):
        d = traer(nombre)
        if d is not None:
            with open(os.path.join(destino, nombre), 'wb') as fh:
                fh.write(d)
    n = 1
    while True:
        d = traer('bloques/%06d.json' % n)
        if d is None:
            break
        if n > max_bloques:
            raise ValueError('el servidor tiene mas de %d bloques (se puede cambiar con --max-bloques)' % max_bloques)
        with open(os.path.join(destino, 'bloques', '%06d.json' % n), 'wb') as fh:
            fh.write(d)
        if d[:1] != b'{':
            break
        n += 1


def main(argv=None):
    import argparse
    import tempfile
    ap = argparse.ArgumentParser(description='Verifica el libro publico de takepower.world (takepower-ledger/1)')
    ap.add_argument('carpeta', nargs='?')
    ap.add_argument('--url')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--estricto', action='store_true')
    ap.add_argument('--edad-max', type=float, default=3.0, dest='edad_max')
    ap.add_argument('--ahora')
    ap.add_argument('--max-bloques', type=int, default=MAX_BLOQUES_DESCARGA, dest='max_bloques')
    a = ap.parse_args(argv)
    if a.max_bloques < 1:
        print('--max-bloques tiene que ser al menos 1', file=sys.stderr)
        return 2
    if bool(a.carpeta) == bool(a.url):
        print('uso: verify_ledger.py CARPETA   o   verify_ledger.py --url https://.../ledger/', file=sys.stderr)
        return 2
    ahora = None
    if a.ahora:
        ahora = hora_a_ms(a.ahora)
        if ahora is None:
            print('--ahora tiene que ser una hora como 2026-11-20T12:00:00.000Z', file=sys.stderr)
            return 2
    tmp = None
    try:
        carpeta = a.carpeta
        if a.url:
            tmp = tempfile.mkdtemp(prefix='ledger-')
            try:
                descargar(a.url, tmp, a.max_bloques)
            except Exception as e:
                print('ERROR no se pudo descargar el libro: %s' % str(e)[:200], file=sys.stderr)
                return 2
            carpeta = tmp
        r = verificar(carpeta, ahora, a.edad_max)
    finally:
        if tmp:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
    if a.json:
        print(json.dumps(r.resumen(), indent=1))
    else:
        for c, t in r.errores:
            print('ERROR [%s] %s' % (c, t))
        for c, t in r.avisos:
            print('AVISO [%s] %s' % (c, t))
        if not r.errores:
            print('OK cadena correcta: %d bloques, %d movimientos, nodo %s..., ultimo bloque %d (%s), saldo %d sats' % (r.bloques, r.movimientos, r.nodo[:12], r.ultimo['bloque'], r.ultimo['motivo'], r.saldo_sat))
            print('   hash del ultimo bloque: %s' % r.ultimo['sha256'])
            print('   (esto comprueba que el libro es coherente y no fue reescrito; no prueba que sea completo ni que cada reclamo sea de una persona real)')
    if r.errores:
        return 1
    if r.avisos:
        return 1 if a.estricto else 3
    return 0


if __name__ == '__main__':
    sys.exit(main())
