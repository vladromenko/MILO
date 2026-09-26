# Contributing

1. Create a branch from `main` and keep changes focused.
2. Run `.venv/bin/python -m pytest -q` before opening a pull request.
3. Add tests for changes to routing, memory, networking, perception, or motion.
4. Keep hardware actions explicit: tests and installation must not move the arm.
5. Never commit models, recordings, personal memory, credentials, generated
   runtime configuration, or device logs.
6. Document physical acceptance separately from hardware-free tests.

For motion changes, include the tested firmware version, affected joints, initial
pose, expected limits, and an attended acceptance result. J6 remains outside the
MILO application command surface.
