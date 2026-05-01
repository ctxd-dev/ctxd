# ctxd

Python SDK and CLI for the `ctxd` platform.

Install:

```bash
pip install ctxd
```

The SDK exposes sync and async clients:

- `Client`
- `AsyncClient`

Authentication:

1. Preferred for headless and scripts: create a user-bound API key through [app.ctxd.dev](app.ctxd.dev)
2. For CLI automation, set `CTXD_API_KEY`
3. `ctxd login` prompts for an API key interactively and stores it in plaintext in the ctxd config directory for future CLI and SDK usage
4. `ctxd status` checks whether an API key is available from the active authentication source

CLI manual:

```bash
export CTXD_API_KEY=...       # Use env-based auth for this shell/session
ctxd --version                # Print the installed ctxd version
ctxd login                    # Prompt for an API key and store it
ctxd status                   # Check whether an API key is configured
ctxd install-app              # Open the app installation page
ctxd search "text:deployment"
ctxd search text:test application:slack
ctxd fetch doc-123
ctxd profile
ctxd logout                   # Remove the stored API key
```

`CTXD_API_KEY` does not require `ctxd login`. Commands such as `ctxd search`, `ctxd fetch`, and `ctxd profile` use the environment variable directly when it is set.

Example:

```python
from ctxd import Client

client = Client(api_key="<api-key>")

results = client.search("text:deployment application:slack")
profile = client.get_profile()
document = client.fetch_document("doc-123")
```

API key example:

```python
from ctxd import Client

client = Client(api_key="<api-key>")
results = client.search("text:deployment application:slack")
```

Async example:

```python
from ctxd import AsyncClient

async with AsyncClient(api_key="<api-key>") as client:
    results = await client.search("text:deployment")
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the package version bump and release
process.
