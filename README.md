# tradebot

Bot de trading **spot, solo compras (long-only) y sin apalancamiento**, conectado a más de 100 exchanges de cripto mediante [ccxt](https://github.com/ccxt/ccxt). Vigila **varias monedas a la vez** (por defecto BTC, ETH, SOL, BNB y XRP) con un único capital y un único control de riesgo.

Tiene tres modos, que usan **exactamente el mismo código de decisión**:

| Modo | Precios | Dinero | Para qué sirve |
|---|---|---|---|
| `backtest` | Históricos | Simulado | Ver si la estrategia habría funcionado en el pasado |
| `paper` | Reales, en directo | Simulado | Ver si funciona *ahora*, sin arriesgar nada |
| `live` (testnet) | Del testnet del exchange | Ficticio del exchange | Probar las órdenes reales contra el exchange |
| `live` (real) | Reales | **Real** | Bloqueado por defecto (ver más abajo) |

> ⚠️ Esto es software, no una garantía de beneficios. La mayoría de estrategias que parecen buenas en un backtest pierden dinero en real. Úsalo bajo tu responsabilidad.

## Instalación

Necesitas Python 3.10 o superior.

```bash
git clone https://github.com/galanmireia/Solidlab
cd Solidlab
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp config/config.example.yaml config/config.yaml
```

## Uso, en este orden

### 1. Backtest con datos reales

```bash
tradebot -c config/config.yaml backtest
```

Descarga las velas del exchange (datos públicos, sin claves) y muestra las métricas. Por defecto guarda el **30 % final como periodo de validación** (`--oos 0.3`). Ajusta los parámetros mirando solo el primer bloque; el segundo es el examen, y si ahí falla, la estrategia no vale.

Opciones útiles:
- `--journal` guarda cada operación en `journal/backtest_trades.csv`.
- `--equity-csv equity.csv` guarda la curva de capital.
- `--synthetic` usa datos inventados (solo para comprobar que el programa funciona).

**Cómo leer los resultados:**
- **Compara siempre con *Buy and hold*.** Si comprar y no hacer nada da más, el bot no aporta.
- **Max drawdown**: la peor caída. Pregúntate si aguantarías verla en tu cuenta real.
- **Num operaciones**: con menos de ~30 operaciones el resultado puede ser pura suerte.
- **Profit factor** por encima de 1.3–1.5 en validación ya es bueno. Valores enormes suelen ser sobreajuste.

### 2. Paper trading (varias semanas como mínimo)

```bash
tradebot -c config/config.yaml paper
```

Usa precios reales en directo con 10 000 USDT simulados. Déjalo funcionando en un ordenador que esté siempre encendido o en un servidor. El estado se guarda en `state/`, así que puedes pararlo con `Ctrl+C` y volver a arrancarlo sin perder la posición. Las operaciones quedan en `journal/paper_*.csv` y los logs en `logs/`.

```bash
tradebot -c config/config.yaml status   # ver posición, stop y estado de riesgo
```

### 3. Testnet (órdenes reales con dinero ficticio)

1. Crea una cuenta en el testnet de tu exchange (por ejemplo [testnet.binance.vision](https://testnet.binance.vision) para Binance spot) y genera claves API.
2. `cp .env.example .env` y pega las claves.
3. Con `exchange.testnet: true` en la configuración:

```bash
tradebot -c config/config.yaml live
```

### 4. Dinero real (cuando decidas, no antes)

Está bloqueado por **tres seguros** a la vez:
1. `exchange.testnet: false` y `live.enabled: true` en la configuración.
2. Variable de entorno `TRADEBOT_LIVE_CONFIRM=YES`.
3. Al arrancar hay que escribir `OPERAR CON DINERO REAL` para confirmar.

**Checklist antes de activarlo:**
- [ ] El backtest es positivo **en el periodo de validación** y supera o se acerca a *buy and hold* con menos drawdown.
- [ ] Al menos 1–2 meses de paper trading con resultados parecidos al backtest.
- [ ] Al menos una semana en testnet sin errores en los logs.
- [ ] **Subcuenta dedicada** en el exchange solo para el bot: el bot calcula el riesgo con todo el saldo libre de la cuenta.
- [ ] Claves API **solo con permiso de trading**: nunca de retiro, y restringidas a tu IP.
- [ ] Empiezas con una cantidad pequeña que puedas perder entera.
- [ ] Sabes cómo cerrar la posición a mano desde la web o la app del exchange.

## Bolsa (acciones y ETFs)

Con `exchange.id: yahoo` el bot usa datos de **Yahoo Finance** (gratis, sin cuenta) para acciones y ETFs: SPY, QQQ, AAPL…, o la Bolsa de Madrid con el sufijo `.MC` (SAN.MC, ITX.MC). Ver `config/railway-stocks.yaml`.

- Solo **backtest y paper trading**: Yahoo da datos, no ejecuta órdenes. Para operar en real haría falta conectar un bróker con API (por ejemplo, Interactive Brokers).
- Usa velas **diarias** (`1d`) o de 1 hora (`1h`): la bolsa solo abre unas horas al día y no los fines de semana. Fuera de horario, el "precio actual" es el último cierre.
- Los precios están ajustados por splits y dividendos.
- No mezcles en un portfolio pares cripto (`BTC/USDT`) con acciones (`AAPL`), ni acciones en divisas distintas (dólares y euros).

**Varios portfolios a la vez:** pasa varias configuraciones y cada una funciona con su propio capital y su propio control de riesgo:

```bash
tradebot -c config/railway.yaml -c config/railway-stocks.yaml paper
# o bien: TRADEBOT_CONFIG=config/railway.yaml,config/railway-stocks.yaml tradebot paper
```

Cada portfolio necesita un `name:` distinto (por ejemplo, "Cripto" y "Bolsa"), que aparece en los mensajes de Telegram.

## En la nube y desde el móvil (Railway + Telegram)

El repositorio incluye `Dockerfile`, `railway.json`, `config/railway.yaml` (cripto) y `config/railway-stocks.yaml` (bolsa), preparados para dejar ambos portfolios en **paper trading 24/7** en Railway, guardando estado, diario y logs en un volumen montado en `/data`.

**Variables de entorno del servicio:**

| Variable | Valor |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token de tu bot (créalo hablando con [@BotFather](https://t.me/BotFather) → `/newbot`) |
| `TELEGRAM_CHAT_ID` | Tu chat id. Si no lo sabes, deja esta variable vacía, escribe al bot y te lo dirá |

**Comandos en Telegram** (solo responde a tu chat):

| Comando | Qué hace |
|---|---|
| `/estado` | Capital total, posiciones abiertas, stops y resultado latente |
| `/precios` | Precio actual de cada moneda vigilada |
| `/operaciones` | Últimas 5 operaciones cerradas |
| `/resumen` | Resultado total y por moneda, aciertos y profit factor |
| `/pausa` | No abrir operaciones nuevas (la abierta mantiene su stop) |
| `/reanudar` | Volver a operar |

Además te avisa automáticamente de cada compra, venta, stop, error y parada por riesgo.

> El servidor solo hace paper trading. El modo con dinero real pide confirmación por teclado y no está pensado para ejecutarse en un servidor.

## Gestión del riesgo

Configurable en `risk:`:

| Regla | Por defecto | Qué hace |
|---|---|---|
| `risk_per_trade` | 1 % | Calcula el tamaño para que, si salta el stop, pierdas como máximo esto (comisiones incluidas) |
| `max_position_pct` | 30 % | Ninguna posición supera esta parte del capital |
| `max_open_positions` | 3 | Como mucho estas monedas compradas a la vez: las criptos suelen caer juntas |
| `max_daily_loss_pct` | 3 % | Tras perder esto en el día (UTC), no abre operaciones hasta el día siguiente |
| `max_drawdown_pct` | 15 % | Si cae esto desde el máximo, **cierra todo y se detiene**. Para reactivarlo hay que borrar el archivo de `state/` tras revisar qué pasó |

La configuración rechaza valores peligrosos: por ejemplo, `risk_per_trade: 1` (el 100 %) da error en lugar de ejecutarse.

**Otras protecciones:**
- Las órdenes se deciden al **cierre** de la vela (de todas las monedas a la vez) y se ejecutan después: no se usan datos del futuro.
- Nunca se opera con la vela que aún no ha cerrado.
- Si hay un error de red al enviar una orden, **no se reintenta** (podría duplicarse): el bot se detiene y pide revisión manual.
- Si el saldo del exchange no coincide con lo que el bot cree tener (por ejemplo, porque vendiste a mano), se detiene.
- Si el exchange no tiene testnet y la configuración dice `testnet: true`, el bot falla en lugar de operar en real.

## Estrategias incluidas

- **`ema_trend`** (seguimiento de tendencia): compra cuando la EMA 20 cruza por encima de la EMA 50 con el precio sobre la EMA 200. El stop está a 3×ATR y sube con el precio (*trailing*). Vende en el cruce contrario o en el stop.
- **`rsi_reversion`** (reversión a la media): compra cuando el RSI está por debajo de 30 dentro de una tendencia alcista (precio sobre la SMA 200). Vende cuando el RSI supera 55, tras 20 velas sin rebote, o en el stop.

Son puntos de partida razonables, **no estrategias probadas como rentables**. Para crear la tuya, copia uno de los archivos de `src/tradebot/strategies/`, regístrala en `strategies/__init__.py` y ponla en la configuración.

## Limitaciones conocidas

- **El stop-loss lo vigila el bot, no el exchange.** Si el ordenador se apaga con una posición abierta, no hay stop. Antes de usar dinero real conviene añadir órdenes stop nativas en el exchange o vigilar que el bot esté siempre en marcha.
- Todas las monedas deben cotizar contra la misma moneda (por ejemplo, USDT).
- Las monedas están muy correlacionadas: diversificar entre criptos reduce menos el riesgo de lo que parece.
- Solo spot y solo compras: no hay cortos ni futuros (es intencionado).
- El backtest supone que las órdenes se ejecutan a la apertura de la siguiente vela con el deslizamiento configurado. En mercados con poca liquidez la realidad será peor.

## Tests

```bash
pytest           # 45 tests: riesgo, ejecución, stops, persistencia, seguros del modo real
ruff check src tests
```

## Estructura

```
src/tradebot/
  config.py          configuración validada
  strategies/        estrategias (solo deciden qué hacer)
  risk.py            tamaño de posición y cortacircuitos
  trader.py          núcleo común por moneda: señales → órdenes → posiciones
  portfolio.py       varias monedas con capital y riesgo compartidos
  stocks.py          datos de bolsa desde Yahoo Finance
  brokers/
    simulated.py     ejecución simulada (backtest y paper)
    ccxt_broker.py   ejecución real o testnet vía ccxt
  backtest.py        backtest vela a vela
  runner.py          bucle en tiempo real con estado persistente
  data.py            descarga de velas y datos sintéticos
  metrics.py         Sharpe, drawdown, profit factor…
  journal.py         diario de operaciones en CSV
  telegram.py        avisos y control por Telegram
  commands.py        comandos /estado, /pausa…
  cli.py             línea de comandos
Dockerfile, railway.json, config/railway.yaml   despliegue en Railway
```
