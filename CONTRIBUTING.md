# Contributing

Thanks for taking a look. This project is small on purpose; changes that keep
it small are the easiest to land.

## Set up

```bash
git clone https://github.com/UAACC/CHAT-API.git && cd CHAT-API
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                # one provider key is enough
uvicorn app.main:app --reload --port 8080
```

No Python? The Docker image is the environment:

```bash
docker build -t chat-api .
docker run --rm -v "$PWD:/src" -w /src chat-api sh -c "pip install -q pytest pytest-asyncio && pytest -q"
```

## Run the tests

```bash
pytest -q
```

The suite runs offline in about two seconds: the model is swapped for a
canned one and storage and vector services are mocked at the boundary. A
change that needs a real API key to test probably belongs behind a mock.

## Make a change

1. Open an issue or a draft PR first for anything beyond a fix, so the
   approach can be discussed before the code exists. Design notes for the
   larger features live in `docs/specs/`; a new subsystem gets one.
2. Add or update tests with the change. Route behaviour is tested over HTTP
   with `TestClient`; services are tested directly.
3. Keep the public surface stable: request and response shapes, environment
   variable names, the SSE event format and the widget's `data-*` attributes
   are all things other people's sites depend on.
4. Run `pytest -q` and `docker build .` before pushing; CI runs both.

## Commit messages

Imperative subject line, under 70 characters, followed by a short body that
says what changed and why. Examples in `git log`.

## Reporting a security issue

See [SECURITY.md](SECURITY.md). Please do not open a public issue for
vulnerabilities.
