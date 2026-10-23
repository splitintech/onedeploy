"""One bounded local calculation. Does not send messages or mutate data."""
import json
print(json.dumps({"status": "completed", "result": sum(range(100))}))
