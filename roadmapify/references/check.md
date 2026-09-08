# roadmap check

```bash
roadmap check "<the approach you are about to take>"
```

| Exit | Meaning |
|------|---------|
| 0 | clear — related items may still print |
| 1 | error |
| 3 | already rejected (MATCH_FLOOR 0.60) |
| 4 | violates a recorded constraint |

Blocking on near-certainty while *surfacing* on overlap is intentional. A bare
CLEAR that throws away related records teaches nothing; a false block teaches
the agent to route around the gate.

Foreign (`trust: foreign`) records never block until
`roadmap doctor --accept <id>`.
