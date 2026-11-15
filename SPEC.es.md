# takepower-ledger/1 - especificacion del formato

Version en ingles: [SPEC.md](SPEC.md). Si alguna vez no coinciden, manda `verify_ledger.py` de este repositorio.

Este documento describe el libro publico del *Baul Comun de la Humanidad* (takepower.world): una lista de **cada pago que entra o sale de su nodo Lightning**, escrita en archivos JSON encadenados con SHA-256, de modo que cualquiera pueda notar si se reescribe el pasado.

Que es, en una frase: un libro **que delata la reescritura** (si un bloque ya publicado cambia, lo ve cualquiera que haya guardado una copia o un sello de tiempo). Que **no** es: una prueba de que el operador haya dicho toda la verdad (ver la seccion 9).

## 1. Archivos

```
bloques/000001.json, 000002.json, ...    un archivo por bloque, seis cifras, desde 000001, sin huecos
indice.json                              lista de todos los bloques con su SHA-256
estado.json                              estado actual: ultima actualizacion, movimientos abiertos, alertas
```

En el espejo publico (este repositorio) la misma estructura esta en la raiz, y cada bloque puede tener al lado su prueba de OpenTimestamps: `bloques/000003.json.ots`.

Codificacion de todos los archivos: UTF-8 que en realidad es **solo ASCII**; JSON (RFC 8259); sin claves repetidas; sin `NaN` ni `Infinity`; enteros entre -(2^53-1) y 2^53-1; los saltos de linea son LF (`\n`), nunca CRLF.

## 2. Archivo de bloque (forma canonica)

Un bloque es valido solo si el archivo es, **byte por byte**, lo que produce este formato (dos programas independientes tienen que obtener los mismos bytes y, por lo tanto, el mismo hash):

```
{
  "formato": "takepower-ledger/1",
  "bloque": 3,
  "nodo": "03ab...(66 hex)",
  "anterior": "9f2c...(64 hex)",
  "cerrado_en": "2026-11-20T12:00:00.123Z",
  "motivo": "cinco_movimientos",
  "rango": [6, 10],
  "saldo_sat": 12345,
  "credito_comisiones_sat": 0,
  "movimientos": [
    {"n":6,"hora":"2026-11-20T11:01:02.003Z","tipo":"entrada","sats":1000,"comision_msat":0,"hash_pago":"ab...(64 hex)"},
    {"n":7,"hora":"...","tipo":"reclamo","sats":-10004,"comision_msat":4000,"hash_pago":"..."}
  ]
}
```

- Dos espacios de sangria para los campos del bloque y cuatro para cada movimiento; un movimiento por linea, compacto (sin espacios despues de `:` ni de `,` dentro de un movimiento); los campos siempre en el orden de arriba; sin espacios al final de linea; el archivo termina con un solo LF.
- Sin movimientos: `"movimientos": []` en una sola linea y `"rango": null`.
- Toda cadena es ASCII simple y no necesita escapes.

### Campos del bloque

| campo | significado |
|---|---|
| `formato` | siempre `takepower-ledger/1` |
| `bloque` | posicion del bloque, desde 1; igual al numero del nombre del archivo |
| `nodo` | clave publica del nodo Lightning: 66 caracteres hexadecimales en minuscula; la misma en todos los bloques |
| `anterior` | SHA-256 (64 hex en minuscula) **de los bytes exactos del archivo del bloque anterior**; 64 ceros en el bloque 1 |
| `cerrado_en` | cuando se escribio el bloque, UTC, con milisegundos, `AAAA-MM-DDTHH:MM:SS.mmmZ`; tiene que ser una hora real del calendario y no anterior a la del bloque previo |
| `motivo` | por que se cerro el bloque: `inicio`, `cinco_movimientos`, `sin_clasificar`, `24_horas` o `latido` (seccion 4) |
| `rango` | `[n del primer movimiento, n del ultimo]`, o `null` si el bloque no tiene movimientos |
| `saldo_sat` | saldo del nodo en sats (entero >= 0) al momento del ultimo movimiento del bloque |
| `credito_comisiones_sat` | credito de comisiones del nodo en sats (entero >= 0) cuando se escribio el bloque |
| `movimientos` | lista de movimientos, del mas antiguo al mas nuevo |

### Campos de un movimiento

| campo | significado |
|---|---|
| `n` | entero >= 1; la numeracion es continua en todo el libro (el primer movimiento del libro es el 1) |
| `hora` | cuando el nodo completo el pago: UTC, milisegundos, el mismo formato que `cerrado_en` |
| `tipo` | `entrada`, `reclamo`, `liquidez`, `gasto`, `retiro` o `sin_clasificar` (seccion 3) |
| `sats` | entero **con signo**: positivo entra al nodo, negativo sale (monto enviado, comisiones de ruta incluidas, tal como lo informa el nodo). `entrada` es >= 0; todos los demas tipos, salvo `sin_clasificar`, son <= 0 |
| `comision_msat` | comisiones que el nodo informa para ese pago, en milisatoshis (entero >= 0) |
| `hash_pago` | hash del pago (64 hex en minuscula), o `null` |
| `tx` | *(solo si existe)* id de la transaccion de Bitcoin, 64 hex en minuscula, para movimientos on-chain que no tienen hash de pago |
| `ref` | *(solo si existe)* `id:` seguido de 1 a 64 caracteres de `0-9 A-Z a-z _ -`, para el raro movimiento que no tiene ni hash de pago ni txid |

Todo movimiento tiene al menos uno de `hash_pago`, `tx`, `ref`. La **clave** de un movimiento es su `hash_pago`; si no tiene, `tx:` + `tx`; si tampoco, su `ref`. Una clave aparece **como maximo una vez** en todo el libro.

No se permite ningun otro campo. El libro nunca contiene direcciones IP, facturas Lightning, direcciones del nodo, descripciones, notas ni mensajes: solo numeros, horas, tipos, montos, comisiones y hashes.

Un movimiento `sin_clasificar` con `sats` igual a 0 significa que el registro del nodo no se pudo leer (no se sabe su sentido); las reglas de salidas de la seccion 4 se aplican solo cuando `sats` es negativo.

## 3. Tipos de movimiento

| `tipo` | que es |
|---|---|
| `entrada` | un pago recibido por el nodo (una donacion o un deposito) |
| `reclamo` | un pago que el grifo envio a una persona que reclamo (coincide con un reclamo de la base del backend que no fallo) |
| `liquidez` | un pago que el nodo hizo para conseguir liquidez de canal (los eventos `auto_liquidity` del propio nodo) |
| `gasto` | un pago saliente que el operador explico despues como un gasto |
| `retiro` | un pago saliente que el operador explico despues como un retiro |
| `sin_clasificar` | un pago saliente que nadie ha explicado |

La clasificacion la hacen las herramientas del operador; terceros no pueden comprobarla. Lo que el libro si garantiza es que **toda salida queda listada**, con hora, monto, comision y hash, y que una salida sin explicar aparece como `sin_clasificar` en vez de quedar fuera.

## 4. Cuando se cierra un bloque

Los movimientos se sellan estrictamente en orden (`n`). Un bloque nuevo lo escribe la primera de estas reglas que se cumpla:

1. **`sin_clasificar`** - una salida de tipo `sin_clasificar` con mas de **15 minutos** (la gracia que le da tiempo al operador de explicar un pago hecho a mano) cierra un bloque que termina en ese movimiento (maximo 5 movimientos). Mientras no pasen sus 15 minutos, ese movimiento frena a si mismo y a todos los posteriores.
2. **`cinco_movimientos`** - hay 5 movimientos esperando: forman un bloque.
3. **`24_horas`** - el movimiento en espera mas antiguo tiene **24 horas** o mas: un bloque con todos los que esperan (de 1 a 4).
4. **`latido`** - no hay movimientos esperando y pasaron **7 dias** desde el ultimo bloque: un bloque sin movimientos (prueba que el libro sigue vivo y muestra el saldo).
5. **`inicio`** - el primerisimo bloque: sin movimientos, solo con el saldo de ese momento. Ningun otro bloque puede ser `inicio`, y el bloque 1 tiene que serlo.

El generador corre una vez por hora, asi que un bloque puede escribirse hasta aproximadamente una hora despues de que su regla se cumpla; un verificador avisa (W02) de un retraso de mas de dos horas.

## 5. `indice.json`

```
{
 "formato": "takepower-ledger/1",
 "bloques": [
  { "bloque": 1, "archivo": "bloques/000001.json", "sha256": "...", "cerrado_en": "...", "motivo": "inicio", "movimientos": 0 },
  ...
 ]
}
```

Una entrada por bloque, en orden: el SHA-256 del archivo, `cerrado_en`, `motivo` y la cantidad de movimientos tienen que coincidir exactamente con el archivo del bloque. Es un indice para lectores; la fuente de verdad son los bloques. Un indice atrasado en un bloque es solo un aviso (el generador escribe primero los bloques y despues el indice).

## 6. `estado.json`

```
{
 "formato": "takepower-ledger/1",
 "nodo": "...",
 "actualizado_en": "2026-11-20T12:00:00.123Z",
 "reglas": { "movimientos_por_bloque": 5, "horas_maximas": 24, "latido_dias": 7, "gracia_minutos": 15 },
 "bloques": 3,
 "ultimo_bloque": { "bloque": 3, "sha256": "...", "cerrado_en": "...", "motivo": "..." },
 "movimientos_sellados": 10,
 "saldo_sat": 12345,
 "credito_comisiones_sat": 0,
 "abierto": [ { "hora": "...", "tipo": "...", "sats": 0, "comision_msat": 0, "hash_pago": "..." } ],
 "alertas": [ "..." ]
}
```

`actualizado_en` es cuando corrio el generador por ultima vez (corre cada hora aunque no haya cambios): asi un lector sabe que el libro esta **vivo**. `abierto` lista los movimientos que existen pero todavia no estan sellados en un bloque (aun no tienen `n`); ninguno puede estar tambien sellado. `alertas` trae los avisos del propio generador (por ejemplo, una diferencia entre el saldo del nodo y la suma de los movimientos).

## 7. Que comprueba un verificador

`verify_ledger.py` lee los archivos y comprueba, para cada bloque: que haya bloques (E01) y que sus nombres no tengan huecos (E02), JSON valido en ASCII sin claves repetidas (E03), el conjunto exacto de campos (E04), los bytes canonicos (E05), `formato` (E06), `bloque` (E07), `nodo` (E08), `anterior` = SHA-256 del archivo previo (E09), las horas (E10), `motivo` (E11), la forma de cada movimiento (E12), la numeracion (E13), `rango` (E14), claves unicas (E15), las reglas de cierre de la seccion 4 (E16) y que ningun movimiento tenga mas de 10 minutos de diferencia posterior a la hora de cierre de su bloque (E17). Un error estructural detiene la lectura de la cadena en ese bloque. Despues `indice.json` (E18) y `estado.json` (E19, E20).

Avisos (la cadena sigue siendo correcta): W01 las cuentas no cuadran (el saldo se movio mas de lo que explican los movimientos; tolerancia `max(5, 2 x movimientos)` sats); W02 un bloque cerrado mas de 2 horas despues de que su regla se cumpliera; W03 el libro no se actualiza hace mas de 3 horas (`--edad-max`); W04/W06 `indice.json`/`estado.json` atrasados uno o mas bloques; W05 alertas del generador; W07 `indice.json`/`estado.json` ilegibles (se ignoran); W08 una hora en el futuro (algun reloj esta mal).

Codigos de salida: `0` todo bien; `1` errores; `2` no se pudo leer / uso incorrecto; `3` cadena correcta pero con avisos (`--estricto` convierte los avisos en `1`).

## 8. Sellos de tiempo y espejo

El hash del ultimo bloque es un compromiso con toda la historia. El espejo publico copia cada bloque nuevo y sella cada uno con [OpenTimestamps](https://opentimestamps.org) (una prueba de que el archivo existia antes de cierto bloque de Bitcoin). Si un bloque se reescribiera despues, el archivo viejo y su prueba seguirian mostrando lo que se habia publicado en su momento, y el historial git del espejo conserva cada version que vio.

**Alertas.** El espejo trata tres avisos como alertas que necesitan a una persona: W01 en un bloque que el espejo todavia no tenia (un hueco ya copiado queda en la historia), W03 y W05. Con una alerta el espejo igual copia, verifica, sella y guarda lo nuevo, y la corrida termina con el codigo de salida 5 (en rojo a proposito, para que la plataforma avise a la persona a cargo). Los demas avisos no hacen fallar la corrida. El aviso en si depende de la configuracion de una cuenta en la plataforma: nada en el repositorio dice a quien se avisa.

## 9. Limites (leer antes de confiar)

- **Delata la reescritura; no es a prueba del operador.** El operador maneja el nodo y el generador. Un movimiento que el operador nunca publico no se ve en los archivos. Lo que el diseno da: cualquiera que haya enviado o recibido un pago puede comprobar que su hash esta en el libro; cada bloque, una vez publicado, queda congelado y sellado en el tiempo; y un libro que deja de actualizarse se nota (W03).
- **El saldo es palabra del operador** cuando viene de un canal privado: nadie mas puede verlo. La comprobacion de cuentas (W01) muestra si los movimientos y el saldo son coherentes entre si, nada mas.
- **No se puede probar que los reclamos vengan de personas reales y distintas.** Un `reclamo` es un pago que envio el grifo; el libro no puede mostrar que la persona detras sea unica ni real.
- **La clasificacion (`gasto`, `retiro`) es una anotacion del propio operador.** Solo `sin_clasificar` es automatica.
- **La vigilancia vale lo que vale su horario.** El espejo corre con el programador de GitHub, que trabaja en la medida de lo posible (una corrida puede llegar tarde o saltarse, y con que frecuencia depende de la carga de GitHub: el historial publico de commits muestra cuando se hizo cada copia de verdad), y GitHub desactiva los flujos programados de un repositorio publico tras 60 dias sin actividad (los commits que hacen las corridas lo mantienen activo mientras el libro este vivo). Un espejo que deja de correr no avisa por si mismo; guarda tu propia copia y compara.
- Una copia que no se compara con el espejo ni con una prueba de OpenTimestamps puede ser reemplazada entera (todos los archivos reescritos de forma coherente). Guarda el hash del ultimo bloque que viste.

## 10. Versiones

`formato` nombra la version. Un cambio que no sea compatible hacia atras recibe un nombre nuevo (`takepower-ledger/2`) y nunca reescribe bloques existentes.
