---
name: sd-image
description: Generate images using FLUX.1-schnell and send via Signal
user-invocable: true
metadata: {"openclaw": {"always": true, "emoji": "🎨"}}
---

## Image Generation (FLUX.1-schnell)

When the user asks you to generate, create, draw, or make an image/picture/photo:

**Use the `exec` tool with this EXACT command format:**

```
/opt/stable-diffusion.cpp/generate-and-send.sh <english prompt describing the image>
```

### Example

User says: "draw a cat on a beach"
You call exec with command:
```
/opt/stable-diffusion.cpp/generate-and-send.sh a cat sitting on a sandy beach, digital art
```

### Important rules

1. The command is `/opt/stable-diffusion.cpp/generate-and-send.sh` — use the FULL absolute path
2. Append the image description in English after the script path
3. Do NOT wrap the prompt in quotes
4. Do NOT use any other command or tool for image generation
5. The script generates the image AND sends it via Signal automatically
6. Image generation takes about 50 seconds — tell the user you are generating
7. After it completes successfully, reply with a short confirmation like "Sent! 🎨"
8. Do NOT output markdown images or base64
9. Do NOT ask for confirmation — just generate immediately
10. If it fails, tell the user honestly what went wrong
