---
name: sd-image
description: Generate images using Stable Diffusion and send via Signal
user-invocable: true
metadata: {"openclaw": {"always": true, "emoji": "🎨"}}
---

## Image Generation (Optional)

If the user asks to generate/create/draw an image, call the exec tool:

```
command: /opt/stable-diffusion.cpp/generate-and-send.sh <prompt words>
```

This script generates the image AND sends it via Signal automatically.
After it completes, just reply with a short confirmation like "Sent! 🎨🦞"

Rules:
- Do NOT output markdown images or base64
- Do NOT ask for confirmation, just generate
- If it fails, tell the user honestly
