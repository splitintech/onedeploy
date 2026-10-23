"""Review this executable deployment source before trusting it.

Install the supported Modal CLI separately. Deploying creates a provider app
and may incur charges. This example uses Modal proxy authentication and contains no private data.
"""
import modal

app = modal.App("example-asgi")
image = modal.Image.debian_slim(python_version="3.12")

@app.function(image=image, max_containers=1, timeout=60)
@modal.asgi_app(requires_proxy_auth=True)
def web():
    async def application(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                event = await receive()
                if event["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif event["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        body = b'{"ok":true}'
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})
    return application
