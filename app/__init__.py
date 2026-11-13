"""Blueprint Evidence: a small web app over the bpqa router.

Ask one question about one drawing, get the answer with its evidence overlay, route, checks and
cost. Run with `.venv\\Scripts\\python -m app` and open http://127.0.0.1:8000.

The app only calls bpqa through its public entry point (`bpqa.router.solve`, as `ask.py` does);
it never changes route or solver code.
"""
