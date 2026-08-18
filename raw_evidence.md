# Build #9 Raw Evidence Report

### FusionEngine Detections Ingested
```json
[
  {
    "feed_id": "uav_eo",
    "pos": [
      1200.0,
      3400.0
    ],
    "class": "armor"
  }
]
```
### FusionEngine Output Tracks
```json
[
  {
    "track_id": "TRK-ARM-001",
    "conf": 0.9,
    "pos": [
      1200.0,
      3400.0
    ]
  }
]
```
### LLM Prompt Sent
```json
[
  {
    "role": "system",
    "content": "You are a tactical AIP staff. Always output valid JSON only. Do not wrap in markdown."
  },
  {
    "role": "user",
    "content": "Analyze the battlefield. Tracks (Known/Suspected Enemy): [{\"track_id\": \"TRK-ARM-001\", \"category\": \"armor\", \"position\": [1200.0, 3400.0], \"velocity\": [0.0, 0.0], \"uncertainty_r\": 5.0, \"confidence\": 0.9, \"sources\": [\"uav_eo\"], \"status\": \"active\", \"first_tick\": 10, \"last_seen_tick\": 10}]. Blue Assets: [{\"id\": \"TB2-01\", \"cls\": \"tb2_recon_uav\", \"pos\": [-1899.9569791093322, -4312.426704077202, 5.59938036043212]}, {\"id\": \"M777-01\", \"cls\": \"m777_howitzer\", \"pos\": [-1107.157047404709, -3158.821964589969, 19.252213153162817]}].\nIdentify information gaps or recommend actions. Generate COAs if there are confirmed targets. Return JSON with 'feed' (list of dicts with type [ISR, ANALYSIS, RECOMMENDATION] and message) and 'coas' (list of dicts: id, action, target, asset, description, confidence, expected_success)."
  }
]
```
### LLM Raw Response
```json
{
  "ok": true,
  "response": {
    "message": {
      "content": "{\"feed\": [], \"coas\": []}"
    }
  }
}
```
### WebSocket Broadcast Packet (STATE_UPDATE)
```json
{
  "tick": 0,
  "blue": [
    [
      -1899.9569791093322,
      -4312.426704077202,
      5.59938036043212
    ],
    [
      -1107.157047404709,
      -3158.821964589969,
      19.252213153162817
    ]
  ],
  "blue_details": [
    {
      "id": "TB2-01",
      "cls": "tb2_recon_uav",
      "pos": [
        -1899.9569791093322,
        -4312.426704077202,
        5.59938036043212
      ]
    },
    {
      "id": "M777-01",
      "cls": "m777_howitzer",
      "pos": [
        -1107.157047404709,
        -3158.821964589969,
        19.252213153162817
      ]
    }
  ],
  "tracks": [
    {
      "track_id": "TRK-ARM-001",
      "category": "armor",
      "position": [
        1200.0,
        3400.0
      ],
      "velocity": [
        0.0,
        0.0
      ],
      "uncertainty_r": 5.0,
      "confidence": 0.9,
      "sources": [
        "uav_eo"
      ],
      "status": "active",
      "first_tick": 10,
      "last_seen_tick": 10
    }
  ],
  "feed": [],
  "approval_queue": [],
  "paused": false
}
```
### ActionBus Dispatch
```json
{
  "action_type": "indirect_fire",
  "agent_id": "M777-01",
  "kwargs": {
    "target": [
      1200,
      3400,
      0
    ],
    "shell_type": "HE"
  }
}
```
### BallisticEngine Invocation
```json
{
  "p0": [
    0.0,
    0.0,
    0.0
  ],
  "target": [
    1200.0,
    3400.0,
    0.0
  ],
  "tof": 40
}
```
