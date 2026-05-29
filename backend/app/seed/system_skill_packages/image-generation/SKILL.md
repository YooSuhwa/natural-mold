---
name: image-generation
description: Use when the user asks to create, generate, draw, render, or make an image, illustration, thumbnail, logo, icon, scene, product mockup, or visual concept from text.
version: 0.1.0
---

# Image Generation

Generate a new image from the user's text request through the configured image endpoint.

## Workflow

1. Turn the user's request into a concise image prompt. Preserve the user's language; if the user asks in Korean, pass a Korean prompt to the script. Read `references/image-studio-prompt.md` if the request is vague or needs stronger composition guidance.
   - For travel-guide requests such as "주말 여행 가이드 이미지 만들어줘", "후쿠오카 여행 가이드맵 만들어줘", or "울산 여행 가이드 이미지 만들어줘", use this Korean prompt shape before running the script:
     `[지역] 관광 가이드맵을 미니멀한 라인 아트 캐릭터를 사용한 모던한 에디토리얼 일러스트레이션으로 만들어 줘. 대표 랜드마크, 로컬 음식, 이동 동선, 주말 여행 분위기를 깔끔한 지도형 구성으로 표현해 줘. 영어 문구를 넣지 말고, 제목과 짧은 한글 라벨만 사용해 줘.`
2. Pick an aspect ratio from the user's intent:
   - `1:1` for icons, profile images, logos, and general square images.
   - `16:9` for thumbnails, banners, slides, and wide scenes.
   - `3:4` for posters, portraits, and vertical social images.
   - `9:16` for mobile wallpapers and stories.
3. Run the script from this skill directory:

```bash
python scripts/generate_image.py --prompt "IMAGE_PROMPT" --aspect-ratio "1:1" --image-size "1K"
```

Use `--aspect-ratio` with one of `1:1`, `16:9`, `9:16`, `4:3`, `3:4`, `4:1`, `1:4`.
Use `--image-size` with one of `512`, `1K`, `2K`, `4K`.
Do not pass `--model` unless the user explicitly asks for a model. The script picks the default model from the bound OpenAI-compatible endpoint:
- OpenRouter (`openrouter.ai`): `openai/gpt-5.4-image-2` via `/chat/completions` with `modalities`.
- Other OpenAI-compatible endpoints: `gpt-image-2` via `/images/generations`.

## Result

The script writes the generated image into `OUTPUTS_DIR` and prints JSON including `output_path`.
After execution, show the generated image with:

```markdown
![image](/api/conversations/<thread_id>/files/<filename>)
```

If generation fails, summarize the error and ask for a smaller or clearer prompt when helpful.
