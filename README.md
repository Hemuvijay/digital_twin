# ARINC 429 ILS Digital Twin — Complete Workflow Reference

> Two labels traced end-to-end: **0o203 (Barometric Altitude)** and **0o173 (ILS Localizer Deviation)**

---

## 1. What This Project Is

This is a software digital twin of an avionics ARINC 429 data bus used during an ILS (Instrument Landing System) approach.
It simulates real Line Replaceable Units (LRUs) — the black boxes on an aircraft — generating ARINC 429 words at the correct
timing, encoding, and format as they would appear on a real aircraft bus.

The tool lets you:
- Run a full ILS approach scenario from a YAML file
- Inject faults (parity errors, SSM failures, bit flips) at precise times
- Validate bus traffic against test vectors (pass/fail)
- Export all bus traffic to CSV or binary for post-analysis
- Decode or encode individual 32-bit ARINC 429 words from the command line

---

## 2. The Two Labels Chosen

| Label | Octal | Source LRU | Bus | Format | Rate | Units |
|---|---|---|---|---|---|---|
| Barometric Altitude | 0o203 | ADIRU_1 | BUS_1 | BNR | 25 Hz | ft |
| ILS Localizer Deviation | 0o173 | ILS_1 | BUS_2 | BNR | 25 Hz | DDM |

These two labels are the most critical during an ILS approach:
- **0o203** tells the autopilot and crew how high the aircraft is
- **0o173** tells the autopilot how far left or right the aircraft is from the runway centerline

---

## 3. Project File Structure

```
main.py                          ← CLI entry point (argparse)
config/
  scenarios/
    ils_approch.yaml             ← Scenario definition (LRUs, phases, faults)
    test_vectors.yaml            ← Pass/fail assertions against live bus traffic
src/
  core/
    word.py                      ← ARINC429Word dataclass (32-bit model)
    codec.py                     ← BNR / BCD / Discrete encode & decode
    labeldb.py                   ← Static label dictionary (ARINC 429 Part 2)
    bus.py                       ← Discrete-event bus scheduler (min-heap)
  lrus/
    models.py                    ← VirtualADIRU, VirtualILS, VirtualRA, VirtualFMC, VirtualTransponder
  engine/
    simulation.py                ← Top-level orchestrator (wires everything)
    scenario.py                  ← YAML parser + phase engine + LRU factory
    fault.py                     ← Fault injection engine
  monitor/
    monitor.py                   ← Real-time label statistics tracker
    logger.py                    ← In-memory word buffer + CSV/binary export
  validation/
    engine.py                    ← Test vector evaluator (PASS/FAIL/MISSED)
  hil/
    bridge.py                    ← Hardware-in-the-loop abstraction layer
```

---

## 4. ARINC 429 Word — 32-Bit Layout

Every piece of data on the bus is a 32-bit word. The bit layout is fixed by the ARINC 429 standard:

```
Bit:  32   31 30   29 28 ... 11   10  9    8 ... 1
      │     │  │    │  │       │    │  │    │      │
      Parity SSM    Data field      SDI    Label (LSB-first)
      (odd)  (2b)   (19 bits BNR)   (2b)   (8 bits, octal)
```

- **Label (bits 1-8):** Identifies what parameter this word carries. Transmitted LSB-first on the wire, so the bit order is reversed in software.
- **SDI (bits 9-10):** Source/Destination Identifier — which specific unit sent this.
- **Data (bits 11-29):** The actual value, encoded as BNR (binary), BCD, or discrete bits.
- **SSM (bits 30-31):** Sign/Status Matrix — tells the receiver if the data is valid.
  - `0b11` = Normal Operation (data is good)
  - `0b00` = Failure Warning (LRU has detected a fault)
  - `0b01` = No Computed Data (LRU is initializing or has no valid output)
  - `0b10` = Functional Test
- **Parity (bit 32):** Odd parity across all 32 bits — hardware integrity check.

---

## 5. Label 0o203 — Barometric Altitude — Full Journey

### 5.1 Where It Is Defined

**File:** `src/core/labeldb.py`

```python
0o203: LabelDefinition(
    label_oct=0o203, name="Barometric Altitude",
    equipment=["ADIRU", "ADC"],
    format="BNR", msb_bit=28, lsb_bit=11,
    resolution=4.0,          # 4 ft per LSB
    range_min=-2000.0,
    range_max=50000.0,
    units="ft", tx_rate_hz=25, speed="HS"
)
```

This is the static specification. It says: label 0o203 carries altitude in BNR format,
data occupies bits 11-28, each LSB = 4 ft, valid range -2000 to 50000 ft, transmitted 25 times/second.

### 5.2 Where the Value Comes From

**File:** `src/lrus/models.py` — class `VirtualADIRU`

The ADIRU holds an internal flight state variable `self.altitude_ft`. Every simulation tick,
`update()` integrates altitude from vertical speed:

```python
def update(self, delta_t_s: float):
    self.altitude_ft += self.vs_fpm * delta_t_s / 60.0
```

The scenario YAML sets the initial value and vertical speed:

```yaml
# config/scenarios/ils_approch.yaml
- id: ADIRU_1
  labels:
    0o203: { rate_hz: 25, initial: 3000.0 }   # starts at 3000 ft

phases:
  - at_s: 0
    params:
      altitude_ft: 3000.0
      vs_fpm: -700.0    # descending at 700 ft/min
```

### 5.3 How the Value Is Encoded Into a 32-Bit Word

**File:** `src/core/codec.py` — class `BNRCodec`

When the scheduler fires for label 0o203, it calls `lru.get_word(0o203)`, which calls:

```python
# Step 1: get current value with noise
value = self.altitude_ft + gaussian_noise(sigma=0.5)   # e.g. 2847.3 ft

# Step 2: scale to integer
scaled = round(2847.3 / 4.0)  = 712

# Step 3: two's complement in 18 bits (bits 11-28 = 18 data bits)
raw_data = 712  →  binary: 000000001011001000

# Step 4: build the 32-bit word
label_reversed = reverse_bits(0o203)   # label bits 1-8
word |= label_reversed                 # bits 1-8
word |= (sdi & 0x03) << 8             # bits 9-10
word |= (raw_data << 10)              # bits 11-28
word |= (0b11 << 29)                  # SSM = Normal (bits 30-31)
word = apply_odd_parity(word)         # set bit 32
```

Result: a single 32-bit integer like `0x5C1B2083`

### 5.4 How It Travels Through the Bus Scheduler

**File:** `src/core/bus.py` — class `BusScheduler`

The scheduler holds a min-heap of `SchedulerEvent` objects. Each event has a `next_tx_us` (next
transmission time in microseconds). For label 0o203 at 25 Hz, the period is 40,000 µs (40 ms).

```
Heap entry for 0o203:
  next_tx_us = 40000      (first fire at t=40ms)
  period_us  = 40000      (every 40ms after that)
  word_gen   = lambda: adiru.get_word(0o203)
```

On each scheduler tick:
1. Pop the earliest event from the heap
2. Enforce minimum inter-word gap (320 µs at HS = 4 × 80 µs bit-time)
3. Call `word_gen()` → get fresh `ARINC429Word`
4. Stamp `timestamp_us`, `bus_id="BUS_1"`, `lru_id="ADIRU_1"`
5. Pass through `FaultInjector.process()` (may modify or drop)
6. Dispatch to all listeners
7. Reschedule: `next_tx_us += 40000`

### 5.5 Fault Injection on 0o203

**File:** `src/engine/fault.py`

The scenario YAML defines:

```yaml
faults:
  - id: F002
    type: PARITY_ERROR
    trigger_time_s: 90
    duration_s: 5
    lru: ADIRU_1
    label: "0o203"
    probability: 0.5
```

Between t=90s and t=95s, each 0o203 word has a 50% chance of having its parity bit flipped:

```python
raw = word.raw_word ^ 0x80000000   # flip bit 32
```

The resulting word has `parity_ok = False`. The monitor counts this as a parity error.

### 5.6 Where It Ends Up

The dispatched word reaches three listeners simultaneously:

**BusMonitor** (`src/monitor/monitor.py`):
- Updates `LabelStats` for key `("BUS_1", "ADIRU_1", 0o203)`
- Tracks: `last_value`, `word_count`, `parity_error_count`, `min_value`, `max_value`, `measured_rate_hz`

**BusLogger** (`src/monitor/logger.py`):
- Appends the word to `self._buffer`
- On export: writes one CSV row with timestamp, hex word, decoded value, SSM, parity status

**ValidationEngine** (`src/validation/engine.py`):
- Checks against TV-001 (at t=5s, altitude must be 2500–3500 ft, SSM=Normal)
- Checks against TV-006 (at t=62s, altitude must be 800–3000 ft, SSM=Normal)

---

## 6. Label 0o173 — ILS Localizer Deviation — Full Journey

### 6.1 Where It Is Defined

**File:** `src/lrus/models.py` — class `VirtualILS`, method `_build_codecs()`

```python
0o173: BNRCodec(0o173, msb_bit=28, lsb_bit=11,
                resolution=0.0000153,   # DDM per LSB
                range_min=-0.2,
                range_max=0.2)
```

DDM (Difference in Depth of Modulation) is the actual physical ILS signal metric.
A real ILS receiver outputs DDM directly on ARINC 429. Full-scale deflection = ±0.155 DDM.

### 6.2 Where the Value Comes From

**File:** `src/lrus/models.py` — class `VirtualILS`

```python
self.localizer_ddm = 0.0   # set by scenario phase or directly

def _compute_value(self, label_oct):
    return {
        0o173: self.localizer_ddm + gaussian_noise(sigma=0.0001),
    }[label_oct]
```

The scenario YAML sets the initial value:

```yaml
- id: ILS_1
  labels:
    0o173: { rate_hz: 25, initial: 0.01 }   # slightly right of centerline
```

### 6.3 Encoding and Bus Transmission

Same BNR encoding pipeline as 0o203, but with different parameters:

```python
# value = 0.01 DDM
scaled = round(0.01 / 0.0000153) = 654
# packed into bits 11-28, SSM=0b11, parity applied
```

The word travels on BUS_2 (separate physical bus from BUS_1 where ADIRU lives).
This mirrors real aircraft wiring — ILS receiver and ADIRU are on different buses.

### 6.4 Fault Injection on 0o173

```yaml
faults:
  - id: F001
    type: SSM_FAILURE_WARNING
    trigger_time_s: 80
    duration_s: 10
    lru: ILS_1
    label: "0o173"
    probability: 1.0
```

Between t=80s and t=90s, every 0o173 word has its SSM bits forced to `0b00` (Failure Warning):

```python
raw = (word.raw_word & 0x9FFFFFFF) | (0b00 << 29)
```

The data bits are unchanged but the SSM says "do not use this data." A real autopilot would
disengage ILS guidance when it sees SSM=Failure Warning on the localizer.

### 6.5 Where It Ends Up

**BusMonitor:** tracks `failure_count` incrementing during t=80–90s window.

**ValidationEngine:**
- TV-003 (at t=5s): LOC deviation must be within ±0.05 DDM, SSM=Normal → PASS
- TV-005 (at t=83s): LOC SSM must be `0b00` (Failure Warning) → PASS (fault is active)

---

## 7. Architectural Flowchart — Both Labels

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                        YAML SCENARIO FILE                                   ║
║  ils_approch.yaml                                                           ║
║  ┌─────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐   ║
║  │ scenario:       │  │ lrus:            │  │ faults:                  │   ║
║  │  duration: 120s │  │  ADIRU_1 BUS_1   │  │  F001: SSM_FAIL t=80s   │   ║
║  │  time_scale:1.0 │  │  ILS_1   BUS_2   │  │  F002: PARITY   t=90s   │   ║
║  └─────────────────┘  │  RA_1    BUS_3   │  └──────────────────────────┘   ║
║                        │  FMC_1   BUS_4   │                                 ║
║                        └──────────────────┘                                 ║
╚══════════════════════════════════════════════════════════════════════════════╝
                                    │
                                    ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║                    ScenarioEngine.load_yaml()                               ║
║  Parses YAML → ScenarioConfig                                               ║
║  build_lrus()  → VirtualADIRU(ADIRU_1, BUS_1)                              ║
║                  VirtualILS(ILS_1, BUS_2)                                   ║
║  build_fault_injector() → FaultInjector with F001, F002                    ║
╚══════════════════════════════════════════════════════════════════════════════╝
                                    │
                                    ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║                    Simulation._setup()                                      ║
║                                                                             ║
║  register_bus("BUS_1", "HS")   register_bus("BUS_2", "HS")                 ║
║                                                                             ║
║  schedule_label(BUS_1, ADIRU_1, 0o203, 25Hz, word_gen)                     ║
║  schedule_label(BUS_2, ILS_1,   0o173, 25Hz, word_gen)                     ║
║                                                                             ║
║  add_listener → BusMonitor.ingest                                           ║
║  add_listener → BusLogger.write                                             ║
║  add_listener → ValidationEngine.check                                      ║
║  set_fault_hook → FaultInjector.process                                     ║
╚══════════════════════════════════════════════════════════════════════════════╝
                                    │
                                    ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║                    Simulation.run()  [100ms chunks]                         ║
║                                                                             ║
║  ┌─────────────────────────────────────────────────────────────────────┐   ║
║  │  BusScheduler.run(chunk_us)                                         │   ║
║  │                                                                     │   ║
║  │  Min-Heap fires at t=40000µs (0o203, 25Hz)                         │   ║
║  │  Min-Heap fires at t=40000µs (0o173, 25Hz)                         │   ║
║  │                                                                     │   ║
║  │  ┌──────────────────────────┐  ┌──────────────────────────────┐    │   ║
║  │  │  LABEL 0o203 PATH        │  │  LABEL 0o173 PATH            │    │   ║
║  │  │                          │  │                              │    │   ║
║  │  │ VirtualADIRU             │  │ VirtualILS                   │    │   ║
║  │  │  altitude_ft = 3000      │  │  localizer_ddm = 0.01        │    │   ║
║  │  │  + gaussian noise(0.5)   │  │  + gaussian noise(0.0001)    │    │   ║
║  │  │  = 2999.7 ft             │  │  = 0.01001 DDM               │    │   ║
║  │  │         │                │  │         │                    │    │   ║
║  │  │         ▼                │  │         ▼                    │    │   ║
║  │  │  BNRCodec.encode()       │  │  BNRCodec.encode()           │    │   ║
║  │  │  2999.7 / 4.0 = 750      │  │  0.01001/0.0000153 = 654     │    │   ║
║  │  │  → bits 11-28            │  │  → bits 11-28                │    │   ║
║  │  │  SSM = 0b11 (Normal)     │  │  SSM = 0b11 (Normal)         │    │   ║
║  │  │  parity applied          │  │  parity applied              │    │   ║
║  │  │  → 0x5C1B2083            │  │  → 0x2A0D4086                │    │   ║
║  │  │         │                │  │         │                    │    │   ║
║  │  │         ▼                │  │         ▼                    │    │   ║
║  │  │  FaultInjector.process() │  │  FaultInjector.process()     │    │   ║
║  │  │  t<90s: pass through     │  │  t>80s: SSM → 0b00 (FAIL)    │    │   ║
║  │  │  t>90s: 50% parity flip  │  │  raw = 0x0A0D4086            │    │   ║
║  │  └──────────────────────────┘  └──────────────────────────────┘    │   ║
║  │                │                              │                     │   ║
║  └────────────────┼──────────────────────────────┼─────────────────────┘   ║
║                   │                              │                          ║
║                   ▼                              ▼                          ║
║  ┌────────────────────────────────────────────────────────────────────┐    ║
║  │                    LISTENER DISPATCH                               │    ║
║  │                                                                    │    ║
║  │  BusMonitor.ingest(word)                                           │    ║
║  │    LabelStats["BUS_1","ADIRU_1",0o203].update()                   │    ║
║  │    LabelStats["BUS_2","ILS_1",  0o173].update()                   │    ║
║  │    → last_value, word_count, parity_errors, measured_rate_hz      │    ║
║  │                                                                    │    ║
║  │  BusLogger.write(word)                                             │    ║
║  │    → appended to in-memory buffer (up to 5M words)                │    ║
║  │                                                                    │    ║
║  │  ValidationEngine.check(word)                                      │    ║
║  │    → TV-001: 0o203 at t=5s  → PASS/FAIL                          │    ║
║  │    → TV-005: 0o173 at t=83s → PASS/FAIL                          │    ║
║  └────────────────────────────────────────────────────────────────────┘    ║
║                                                                             ║
║  ScenarioEngine.update(sim_time_s)                                          ║
║    → phase transition at t=60s: altitude_ft=1500, vs_fpm=-600              ║
║    → VirtualADIRU.update(delta_t) integrates new vs_fpm                    ║
╚══════════════════════════════════════════════════════════════════════════════╝
                                    │
                                    ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║                         SIMULATION END                                      ║
║                                                                             ║
║  BusMonitor.print_table()      → console table of all labels               ║
║  ValidationEngine.print_report() → PASS/FAIL per test vector               ║
║  BusLogger.to_csv("output.csv")  → one row per word                        ║
║  BusLogger.to_binary("out.bin")  → 14 bytes per word                       ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

---

## 8. Data Transformation at Each Stage

### Label 0o203 — Altitude 3000 ft

| Stage | What happens | Value / Result |
|---|---|---|
| YAML | `initial: 3000.0` | `altitude_ft = 3000.0` |
| `VirtualADIRU._compute_value()` | Add Gaussian noise | `3000.3 ft` |
| `BNRCodec.encode()` | Divide by resolution 4.0, round | `scaled = 750` |
| Bit packing | Shift left 10 bits (lsb_bit-1=10) | `750 << 10 = 0x000BB800` |
| SSM | OR in `0b11 << 29` | `0x60000000` |
| Label | Reverse bits of 0o203=0x83 → 0xC1 | `0x000000C1` |
| Parity | Count 1-bits, set bit 32 if even | Final: `0x5C1BB8C1` |
| FaultInjector | t<90s, pass through | Unchanged |
| BusMonitor | Decode back: `750 × 4.0 = 3000 ft` | `last_value = 3000.0` |

### Label 0o173 — Localizer 0.01 DDM

| Stage | What happens | Value / Result |
|---|---|---|
| YAML | `initial: 0.01` | `localizer_ddm = 0.01` |
| `VirtualILS._compute_value()` | Add noise | `0.010012 DDM` |
| `BNRCodec.encode()` | Divide by 0.0000153, round | `scaled = 654` |
| Bit packing | `654 << 10` | `0x000A3800` |
| SSM | `0b11 << 29` | Normal |
| FaultInjector t>80s | `SSM → 0b00` | `0x000A3800` (data unchanged, SSM = Failure) |
| BusMonitor | `failure_count++` | Logged as failure |
| ValidationEngine TV-005 | SSM == `0b00` at t=83s | PASS |

---

## 9. Debug Mode — Variable and Function Call Trace

This section shows exactly what happens at the code level when you step through the simulation
in a debugger (VS Code, PyCharm, or `pdb`). Set breakpoints at the locations marked `[BP]`.

### 9.1 Entry Point

```
main.py → cmd_run(args)
  args.scenario = "config/scenarios/ils_approch.yaml"
  args.vectors  = "config/scenarios/test_vectors.yaml"

  [BP] sim = Simulation()
       sim.real_time  = False
       sim.time_scale = 1.0
       sim.scenario   = ScenarioEngine()      ← empty, no YAML loaded yet
       sim.scheduler  = BusScheduler()        ← empty heap []
       sim.monitor    = BusMonitor()          ← _stats = {}
       sim.logger     = BusLogger()           ← _buffer = []
       sim.validator  = ValidationEngine()    ← _vectors = []
```

### 9.2 Loading the Scenario

```
[BP] sim.load_scenario("config/scenarios/ils_approch.yaml")
  → Simulation.load_scenario()
    → self.scenario.load_yaml(path)
      → yaml.safe_load(f)  returns raw dict
      → ScenarioEngine._parse(raw)
        config.name        = "ILS CAT I Approach"
        config.duration_s  = 120.0
        config.lru_configs = [
          {"id":"ADIRU_1","type":"ADIRU","bus":"BUS_1","speed":"HS","labels":{...}},
          {"id":"ILS_1",  "type":"ILS",  "bus":"BUS_2","speed":"HS","labels":{...}},
          ...
        ]
        config.phases = [FlightPhase(at_s=0,...), FlightPhase(at_s=60,...)]
        config.fault_configs = [{"id":"F001",...}, {"id":"F002",...}]
    → self._setup()
```

### 9.3 Setup — LRU Instantiation

```
[BP] ScenarioEngine.build_lrus()
  lru_cfg = {"id":"ADIRU_1","type":"ADIRU","bus":"BUS_1","sdi":0}
  factory = LRU_FACTORY["ADIRU"] = VirtualADIRU
  lru = VirtualADIRU(lru_id="ADIRU_1", bus_id="BUS_1", sdi=0)
    → VirtualADIRU.__init__()
        self.altitude_ft      = 0.0   (will be overridden)
        self.vs_fpm           = 0.0
        self._state           = LRUState.INITIALIZING
        self._init_elapsed_s  = 0.0
        self._init_duration_s = 2.0
        self._codecs = {}
        self._build_codecs()
          self._codecs[0o203] = BNRCodec(0o203, 28, 11, 4.0, -2000.0, 50000.0)
          self._codecs[0o204] = BNRCodec(0o204, 28, 11, 0.0625, 0.0, 500.0)
          ...

  # Apply initial values from YAML
  lru.set_flight_params(altitude_ft=3000.0, vs_fpm=-700.0, ias_kts=140.0)
    self.altitude_ft = 3000.0
    self.vs_fpm      = -700.0
    self.ias_kts     = 140.0
    self.tas_kts     = 140.0 * (1 + 3000/1000 * 0.02) = 148.4

  self.lrus["ADIRU_1"] = lru
```

### 9.4 Setup — Bus Registration and Label Scheduling

```
[BP] scheduler.register_bus("BUS_1", "HS")
  self._buses["BUS_1"] = BusState(speed="HS", last_word_end_us=0)

[BP] scheduler.schedule_label("BUS_1", "ADIRU_1", 0o203, 25.0, word_gen)
  period_us = 1_000_000 / 25.0 = 40000
  event = SchedulerEvent(
    next_tx_us = 0 + offset_us,
    period_us  = 40000,
    lru_id     = "ADIRU_1",
    label_oct  = 0o203,
    bus_id     = "BUS_1",
    word_gen   = <lambda: adiru.get_word(0o203, ...)>
  )
  heapq.heappush(self._heap, event)
  # heap now has one entry for 0o203
```

### 9.5 Simulation Run — First Word for 0o203

```
[BP] BusScheduler.run(chunk_us=100000)
  event = heapq.heappop(self._heap)
    event.next_tx_us = 0
    event.label_oct  = 0o203
    event.bus_id     = "BUS_1"

  bus = self._buses["BUS_1"]
  actual_tx_us = bus.earliest_tx_us(0)
    earliest = 0 + 40 = 40   (min gap = HS_MIN_GAP_US = 40)
    return max(0, 40) = 40

  self._clock_us = 40

  [BP] word = event.word_gen()
    → adiru.get_word(0o203, timestamp_us=40)
      codec = self._codecs[0o203]   # BNRCodec
      ssm   = self._state.to_ssm()
        # state is INITIALIZING (elapsed=0 < 2.0s)
        # ssm = SSM_NCD = 0b01
      value = 0.0   (NCD, not computing)
      return codec.encode(0.0, ssm=0b01, sdi=0, lru_id="ADIRU_1", bus_id="BUS_1", timestamp_us=40)

  word.timestamp_us = 40
  word.bus_id       = "BUS_1"
  word.lru_id       = "ADIRU_1"

  [BP] word = self._fault_hook(word)   # FaultInjector.process()
    sim_time_s = 40 / 1_000_000 = 0.000040s
    F001 trigger_time_s=80 → not yet triggered
    F002 trigger_time_s=90 → not yet triggered
    return word unchanged

  bus.record_transmission(40)
    self.last_word_end_us = 40 + 320 = 360

  for cb in self._listeners:
    cb(word)   # BusMonitor, BusLogger, ValidationEngine

  event.next_tx_us += 40000   # = 40040
  heapq.heappush(self._heap, event)
```

### 9.6 After 2 Seconds — LRU Transitions to NORMAL

```
[BP] ScenarioEngine.update(sim_time_s=2.1, delta_t_s=0.1)
  for lru in self.lrus.values():
    lru.update(0.1)
      → VirtualADIRU.update(0.1)
        super().update(0.1)   # VirtualLRU.update
          self._init_elapsed_s += 0.1   # now = 2.1
          if 2.1 >= 2.0:
            self.set_state(LRUState.NORMAL)
              self._state = LRUState.NORMAL
        # now in NORMAL, integrate physics
        self.altitude_ft += (-700) * 0.1 / 60.0 = -1.167
        # altitude_ft = 3000.0 - 1.167 = 2998.833
```

### 9.7 First NORMAL Word for 0o203

```
[BP] word = adiru.get_word(0o203, timestamp_us=2100000)
  ssm   = LRUState.NORMAL.to_ssm() = 0b11
  value = self._compute_value(0o203)
    = self.altitude_ft + noise
    = 2998.833 + gauss(0, 0.5)
    = 2999.1  (example)

  [BP] codec.encode(2999.1, ssm=0b11, sdi=0, ...)
    value = max(-2000, min(50000, 2999.1)) = 2999.1
    scaled = round(2999.1 / 4.0) = round(749.775) = 750
    raw_data = twos_complement(750, 18) = 750  (positive, no change)
    label_rev = reverse_bits(0o203=0x83=10000011) = 11000001 = 0xC1
    word  = 0xC1
    word |= (0 & 0x03) << 8    = 0x0000C1   (SDI=0)
    word |= 750 << 10          = 0x000BB800 | 0xC1 = 0x000BB8C1
    word |= (0b11) << 29       = 0x600BB8C1
    word  = apply_odd_parity(0x600BB8C1)
      count 1-bits in 0x600BB8C1 = 14 (even)
      set bit 32: word |= 0x80000000
      final = 0xE00BB8C1

  w = ARINC429Word.from_raw(0xE00BB8C1, ...)
    label_oct     = 0o203
    data_raw      = 750
    ssm           = 0b11
    parity_ok     = True
    decoded_value = 2999.1
```

### 9.8 Fault Injection at t=90s on 0o203

```
[BP] FaultInjector.process(word)  at sim_time_s = 90.5
  for f in self._faults:
    f = F002 (PARITY_ERROR, trigger=90, duration=5, lru=ADIRU_1, label=0o203, prob=0.5)
    not f.triggered and 90.5 >= 90.0 → f.active=True, f.triggered=True

  word matches: lru_id="ADIRU_1", label_oct=0o203
  self._rng.random() = 0.31  < 0.5  → apply fault

  [BP] self._apply_fault(word, F002)
    fault_type = PARITY_ERROR
    raw = word.raw_word ^ 0x80000000   # flip bit 32
    return ARINC429Word.from_raw(raw, ...)
      parity_ok = False   ← parity check now fails
```

### 9.9 Validation Check

```
[BP] ValidationEngine.check(word)  for TV-001
  tv.label_oct = 0o203, tv.at_sim_time_s = 5.0
  word.label_oct = 0o203 ✓
  word.bus_id = "BUS_1" ✓
  sim_time_s = 5.0 ≥ 5.0 ✓  (within window)

  tv.actual_value = word.decoded_value = 2999.1
  tv.actual_ssm   = 0b11

  expected_parity_ok = True, word.parity_ok = True ✓
  expected_ssm = 0b11, word.ssm = 0b11 ✓
  value_min=2500, value_max=3500, actual=2999.1 ✓

  tv.result = VectorResult.PASS
```

### 9.10 Key Breakpoint Locations Summary

| File | Line / Function | What to inspect |
|---|---|---|
| `main.py` | `cmd_run()` | `args` namespace |
| `src/engine/simulation.py` | `Simulation._setup()` | `lrus`, `scheduled_labels` |
| `src/engine/scenario.py` | `ScenarioEngine.build_lrus()` | `lru._codecs`, `lru.altitude_ft` |
| `src/core/bus.py` | `BusScheduler.run()` | `event.next_tx_us`, `actual_tx_us` |
| `src/lrus/models.py` | `VirtualADIRU.get_word()` | `ssm`, `value`, codec call |
| `src/core/codec.py` | `BNRCodec.encode()` | `scaled`, `raw_data`, `word` bits |
| `src/core/word.py` | `ARINC429Word.from_raw()` | `label_oct`, `ssm`, `parity_ok` |
| `src/engine/fault.py` | `FaultInjector.process()` | `f.active`, `f.triggered`, rng value |
| `src/monitor/monitor.py` | `LabelStats.update()` | `last_value`, `parity_error_count` |
| `src/validation/engine.py` | `ValidationEngine.check()` | `tv.result`, `failures` list |

---

## 10. How to Run

```bash
# Full ILS approach scenario with test vectors
python main.py run --scenario config/scenarios/ils_approch.yaml --vectors config/scenarios/test_vectors.yaml

# Same, also export CSV
python main.py run --scenario config/scenarios/ils_approch.yaml --vectors config/scenarios/test_vectors.yaml --csv output.csv

# 10-second built-in demo (no YAML needed)
python main.py demo

# Decode a raw hex word
python main.py decode 0xE00BB8C1

# Encode altitude 3000ft into label 0o203
python main.py encode --label 0o203 --value 3000.0 --msb 28 --lsb 11 --res 4.0

# Run unit tests
python main.py test
```

---

## 11. Key Technical Concepts — Manager Summary

| Concept | What it means in plain terms |
|---|---|
| ARINC 429 | The standard wiring protocol used on commercial aircraft to send data between avionics boxes. One sender, up to 20 receivers, 32-bit words. |
| Digital Twin | A software copy of the real hardware that behaves identically — same data format, same timing, same failure modes. |
| Label | A number (in octal) that identifies what parameter a word carries. Like a channel number. 0o203 = altitude, 0o173 = localizer. |
| BNR Encoding | Binary encoding: the engineering value is scaled to an integer and packed into specific bits of the 32-bit word. |
| SSM | A 2-bit field that tells receivers whether the data is valid. If SSM ≠ Normal, the autopilot ignores the data. |
| Fault Injection | The ability to deliberately corrupt words at a specific time to test how downstream systems react. |
| Test Vectors | Pre-defined assertions: "at time T, label L on bus B must have value between X and Y." Automatically evaluated during simulation. |
| HIL Bridge | Hardware-in-the-Loop: the twin can connect to a real ARINC 429 card and exchange words with actual avionics hardware. |

---

*Generated from codebase analysis of the ARINC 429 ILS Digital Twin project.*
