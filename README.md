Here is the complete UI design plan and implementation for both applications.

UI Design — What a Professional Avionics Tool Looks Like
Real avionics ground support tools like AIM BusTools, Astronics AceXtreme, and DDC Bus Tools all share the same visual language — dark background, monospace data fonts, color-coded status indicators, and live updating tables. That is the standard we follow.

Transmitter Application — UI Layout
╔══════════════════════════════════════════════════════════════════════════════╗
║  ARINC 429 Digital Twin — Transmitter                          [_][□][X]    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  CONFIGURATION                                                               ║
║  ┌─────────────────────────────────┐  ┌──────────────────────────────────┐  ║
║  │ Scenario File  [Browse]         │  │ Receiver IP   [192.168.1.50    ] │  ║
║  │ ils_approch.yaml                │  │ Port          [5429            ] │  ║
║  │ Duration (s)   [120           ] │  │ [Connect & Run]  [Stop]          │  ║
║  └─────────────────────────────────┘  └──────────────────────────────────┘  ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  STATUS BAR                                                                  ║
║  ● RUNNING   Sim Time: 47.3s / 120.0s  ████████████░░░░░░░░  39%           ║
║  Words TX: 18,940   Parity Errors: 12   Active Faults: 0                    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  LIVE BUS MONITOR                                                            ║
║  Channel    LRU        Label   Name                Value      Units  SSM    ║
║  ─────────────────────────────────────────────────────────────────────────  ║
║  CHANNEL_1  ADIRU_1    0o203   Barometric Alt      2647.3     ft     ● NRM  ║
║  CHANNEL_1  ADIRU_1    0o204   Indicated Airspeed  139.9      kts    ● NRM  ║
║  CHANNEL_1  ADIRU_1    0o101   Pitch Attitude      2.48       deg    ● NRM  ║
║  CHANNEL_2  ILS_1      0o173   Localizer Dev       0.0098     DDM    ● NRM  ║
║  CHANNEL_2  ILS_1      0o175   Glideslope Dev      0.0049     DDM    ● NRM  ║
║  CHANNEL_3  RA_1       0o164   Radio Altitude      2499.6     ft     ● NRM  ║
║  CHANNEL_4  FMC_1      0o106   Cross Track         0.0072     nm     ● NRM  ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  FAULT INJECTION                                                             ║
║  [Inject Fault ▼]  LRU:[ADIRU_1▼]  Label:[0o203▼]  Type:[PARITY_ERROR▼]   ║
║  Duration(s):[5]  Probability:[1.0]  [Fire Now]                             ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  EVENT LOG                                                                   ║
║  [14:23:01.040]  Simulation started — ILS CAT I Approach                    ║
║  [14:23:01.041]  Connected to 192.168.1.50:5429                             ║
║  [14:23:43.040]  FAULT F001 activated — ILS_1 0o173 SSM_FAILURE_WARNING     ║
║  [14:23:53.040]  FAULT F001 deactivated                                     ║
╚══════════════════════════════════════════════════════════════════════════════╝
Receiver Application — UI Layout
╔══════════════════════════════════════════════════════════════════════════════╗
║  ARINC 429 Digital Twin — Receiver                             [_][□][X]    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  CONNECTION                                                                  ║
║  Port [5429]  [Start Listening]  [Export CSV]  [Load Vectors]  [Stop]       ║
║  ● CONNECTED from 192.168.1.10   Words RX: 18,940   Parity Errors: 12      ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  LIVE BUS MONITOR                                                            ║
║  Channel    TX LRU     Label   Name                Value      Units  SSM    ║
║  ─────────────────────────────────────────────────────────────────────────  ║
║  CHANNEL_1  ADIRU_1    0o203   Barometric Alt      2647.3     ft     ● NRM  ║
║  CHANNEL_1  ADIRU_1    0o204   Indicated Airspeed  139.9      kts    ● NRM  ║
║  CHANNEL_2  ILS_1      0o173   Localizer Dev       0.0098     DDM    ● FAIL ║
║  CHANNEL_3  RA_1       0o164   Radio Altitude      2499.6     ft     ● NRM  ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  VALIDATION RESULTS                                                          ║
║  ID       Description                              Result   Value           ║
║  TV-001   Baro altitude nominal at approach start  ✓ PASS   2941.2 ft      ║
║  TV-002   IAS within approach speed range          ✓ PASS   139.9 kts      ║
║  TV-003   Localizer deviation within limits        ✓ PASS   0.0098 DDM     ║
║  TV-005   ILS LOC failure warning active           ● PEND   —              ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  EVENT LOG                                                                   ║
║  [14:23:01.041]  Receiver started — listening on port 5429                  ║
║  [14:23:01.042]  Transmitter connected from 192.168.1.10                    ║
║  [14:23:43.040]  TV-001 PASS — altitude 2941.2 ft                          ║
║  [14:23:43.041]  PARITY ERROR detected — ADIRU_1 0o203                     ║
╚══════════════════════════════════════════════════════════════════════════════╝
Now implement both applications:


2 edits to file

gui_transmitter.py
47


Error(s) while editing

gui_transmitter.py

Either the text arg was not provided or text content provided exceeded the write file limit of 50 lines. The agent has seen this error and will try a different approach to write the file if needed.
User accepted edits to

gui_transmitter.py
47


User accepted edits to

gui_transmitter.py
407


User accepted creation of

gui_receiver.py
388


Checked diagnostics

gui_transmitter.py

gui_receiver.py
Both files are clean. Here is the complete practical guide.

How to Run the GUI Applications
Receiver PC — start first
python gui_receiver.py
The receiver window opens
Port is pre-filled as 5429 — change if needed
Optionally click Load Vectors and select 
test_vectors.yaml
Click ▶ Start Listening
Status changes to LISTENING in yellow — waiting for transmitter
Transmitter PC — start after receiver is listening
python gui_transmitter.py
The transmitter window opens
Click Browse and select 
ils_approch.yaml
Enter the receiver PC's IP address in the Receiver IP field
Leave Duration blank to use the YAML value (120s)
Click ▶ Connect & Run
Both windows will show live data updating every 100ms.

What Each UI Component Does
TRANSMITTER                          RECEIVER
─────────────────────────────────    ─────────────────────────────────
Browse — pick YAML scenario          Load Vectors — pick test vectors YAML
Receiver IP + Port — target          Port — which port to listen on
Connect & Run — starts everything    Start Listening — opens TCP server
Stop — halts simulation              Stop — closes connection
Progress bar — sim time %            Words RX counter — live count
Words TX counter — live count        Parity Errors — highlighted red if > 0
Parity Errors — red if > 0           Live Bus Monitor — same as transmitter
Live Bus Monitor — all labels live   Validation Results — PASS/FAIL per vector
Fault Injection — fire faults live   Export CSV — save received data
Event Log — timestamped events       Event Log — timestamped events
Same PC Testing (Loopback)
Open two terminal windows or just double-click both scripts:


# Window 1
python gui_receiver.py

# Window 2
python gui_transmitter.py
# Set Receiver IP to: 127.0.0.1
