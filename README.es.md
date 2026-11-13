# takepower-ledger

Espejo público del libro del *Baúl Común de la Humanidad* ([takepower.world](https://takepower.world/)): la lista de **todos los pagos que entran o salen de su nodo Lightning**, escrita en archivos JSON encadenados con SHA-256 y sellados con [OpenTimestamps](https://opentimestamps.org).

English: [README.md](README.md) · Formato: [SPEC.es.md](SPEC.es.md) ([SPEC.md](SPEC.md), en inglés)

## Qué es este repositorio

- `bloques/000001.json, 000002.json, ...` - los bloques. Cada uno guarda hasta 5 movimientos (hora, tipo, monto, comisión, hash del pago) y el SHA-256 del archivo del bloque anterior.
- `bloques/NNNNNN.json.ots` - la prueba de OpenTimestamps de ese bloque (prueba de que el archivo existía antes de un bloque determinado de Bitcoin).
- `indice.json`, `estado.json` - índice de todos los bloques y estado actual (cuándo actualizó el servidor el libro por última vez, saldo, movimientos aún sin sellar, alertas).
- `verify_ledger.py` - un verificador independiente: un solo archivo, Python 3.8 o superior, solo biblioteca estándar. Se escribió a partir de `SPEC.es.md`, no del código del generador.
- `mirror_sync.py` + `.github/workflows/espejo.yml` - lo que copia, verifica y sella el libro cada hora (más abajo).

La copia viva está en <https://takepower.world/ledger/>. Este repositorio es una segunda copia en la que el servidor **no puede escribir**: GitHub jala desde el servidor, nunca al revés, y el flujo de trabajo no tiene ningún secreto del servidor.

## Compruébalo tú mismo (2 minutos)

```
git clone https://github.com/Rapayet/takepower-ledger
cd takepower-ledger
python3 verify_ledger.py .
```

(En Windows: `py -3 verify_ledger.py .`)

Eso lee todos los bloques y comprueba la cadena, los bytes canónicos de cada archivo, las reglas de cierre, la numeración, el índice y el estado. Imprime `OK` o la lista de problemas, con un código por comprobación (errores E01...E20, avisos W01...W08, descritos en [SPEC.es.md](SPEC.es.md), sección 7).

Para comprobar la copia del servidor en vez de la de este repositorio: `python3 verify_ledger.py --url https://takepower.world/ledger/`. Para comparar ambas: el SHA-256 del último bloque está en `estado.json` (`ultimo_bloque.sha256`) de cada lado.

Códigos de salida: `0` todo bien · `1` errores · `2` no se pudo leer / uso incorrecto · `3` la cadena es correcta pero hay avisos (`--estricto` convierte los avisos en errores). Con `--json` se obtiene un resumen para máquinas.

### Comprobar un sello de tiempo

```
sha256sum bloques/000003.json          # el hash que se sella
```

Luego, o bien arrastra `bloques/000003.json` y `bloques/000003.json.ots` a <https://opentimestamps.org> (lo fácil), o usa el cliente con un nodo de Bitcoin propio (máxima confianza): `pip install opentimestamps-client==0.8.0` y `ots verify bloques/000003.json.ots`.

Una prueba está *pendiente* durante unas horas después de escribirse el bloque (los calendarios prometieron incluirla) y queda *completa* cuando Bitcoin la confirma; el flujo de trabajo completa las pruebas pendientes cada hora.

## Qué hace el espejo cada hora

1. Descarga el libro publicado y **lo verifica**. Si no pasa, no se copia nada y la corrida queda en rojo.
2. Compara con lo que este repositorio ya tiene. **Un bloque ya copiado nunca se sobrescribe.** Si el servidor cambia un bloque que ya había publicado, o publica menos bloques de los que este repositorio tiene, la corrida se detiene con un error (`REESCRITURA` / `TRUNCAMIENTO`) y la evidencia queda en el historial de git.
3. Copia los bloques nuevos, `indice.json` y `estado.json`, y vuelve a verificar el resultado.
4. Sella cada bloque nuevo (`ots stamp`) y completa las pruebas pendientes (`ots upgrade`).
5. Hace commit como `github-actions[bot]`. El mensaje dice qué cambió (`espejo: +1 bloque(s) hasta el 7 (cinco_movimientos), 31 movimientos, 4e1f0a9c2b7d`).

`estado.json` cambia cada hora (lleva la hora de la última actualización), así que **hay un commit cada hora**. Es a propósito: el historial de commits es un registro de cuándo estuvo vivo el servidor, y un hueco en él se ve. Una corrida en rojo significa que hace falta una persona: abre el registro de la corrida, dice qué comprobación falló.

### Alertas: cuando una corrida termina en rojo a propósito

Algunos avisos del verificador significan que una persona debe mirar, así que el espejo los convierte en una **alerta**:

- **W01** las cuentas de un bloque *nuevo* no cuadran (un hueco que ya se copió queda en el historial y no se repite cada hora);
- **W03** el libro no se actualiza desde hace más de 3 horas;
- **W05** el propio generador avisa de algo (por ejemplo, una diferencia entre el saldo del nodo y los movimientos).

Con una alerta el espejo igual copia, verifica, sella y hace commit de lo nuevo (una alerta nunca frena la copia), pero la corrida termina **en rojo a propósito**: código de salida `5`, líneas `::error::ALERTA ...` en el registro y un paso aparte `ALERTA` en el flujo de trabajo. GitHub envía entonces su aviso habitual de «corrida fallida» a quien creó el flujo de trabajo (o editó por última vez su horario), según las notificaciones de esa persona: **este repositorio no contiene ninguna dirección de correo**. Cualquier otro aviso (W02, W04, W06, W07, W08) sigue siendo una nota amarilla y no hace fallar la corrida. Mientras dure una alerta, cada corrida horaria sale en rojo. Todo lo que viene del libro publicado se imprime en una sola línea, sin caracteres de control, de modo que un servidor comprometido no pueda falsificar comandos en el registro de la corrida.

Códigos de salida de `mirror_sync.py`: `0` todo bien · `1` el libro publicado no verifica, o reescribió o truncó un bloque ya copiado (no se toca nada) · `2` no se pudo descargar · `4` se copió, pero un bloque no se pudo sellar (la próxima corrida lo reintenta) · `5` se copió y se selló, pero el libro trae una alerta. Si hay más de uno, gana el más grave: `1` > `5` > `4`.

Versiones fijas: el flujo de trabajo usa `actions/checkout` por el SHA del commit e instala el cliente de OpenTimestamps con `pip install --require-hashes` desde `requirements-ots.txt` (versiones exactas y SHA-256 de cada archivo), de modo que una versión nueva y comprometida de una dependencia no llega sola al flujo.

## Corre tu propio espejo

Haz un fork de este repositorio, entra en *Actions* y activa los flujos de trabajo. Tu fork copiará el libro cada hora y guardará su propio historial y sus propias pruebas; una corrida en rojo en tu fork te la avisa GitHub a ti, según tus propias notificaciones. Cuantas más copias independientes existan, más difícil es reescribir el pasado sin que nadie lo note.

## Qué prueba esto, y qué no

El libro es **a prueba de reescritura** (se nota si se cambia): una vez publicado un bloque, cambiarlo se ve para quien guardó una copia o un sello de tiempo. **No** es a prueba de operador:

- El operador maneja el nodo y el generador. Un pago que el operador nunca publicó no aparece en los archivos. Lo que puedes hacer: si enviaste o recibiste un pago, comprueba que su hash está en el libro.
- El saldo del nodo es palabra del operador cuando está en un canal privado; la comprobación W01 solo muestra que los movimientos y el saldo son coherentes entre sí.
- Un `reclamo` es un pago que envió el grifo. El libro no puede demostrar que la persona detrás sea única ni real.
- La clasificación (`gasto`, `retiro`) es una anotación del propio operador, hecha después. Solo `sin_clasificar` es automática: un pago de salida que nadie explicó figura así, no se esconde.
- **La vigilancia vale lo que vale su horario.** El espejo corre con el programador de GitHub, que trabaja «en la medida de lo posible» (una corrida puede llegar tarde o, rara vez, saltarse), y GitHub desactiva los flujos programados de un repositorio público tras 60 días sin actividad (los commits de cada hora lo mantienen activo mientras el libro esté vivo). Un espejo que deja de correr no avisa por sí mismo; guarda tu propia copia y compara.
- Una copia que no se compara con otra copia o con un sello de tiempo puede reemplazarse entera. Guarda el hash del último bloque que viste.

La lista completa está en [SPEC.es.md](SPEC.es.md), sección 9.

## Avisar de un problema

Abre un issue en este repositorio. Si encuentras la forma de que `verify_ledger.py` acepte un libro que debería rechazar (o al revés), ese es el informe más útil que hay.
