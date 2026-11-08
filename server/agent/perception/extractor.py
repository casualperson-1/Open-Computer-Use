import io
from typing import List, Dict, Any, Optional
from PIL import Image, ImageDraw, ImageFont
from playwright.async_api import Page
from .schema import DOMElement, PerceptionState

EXTRACT_ELEMENTS_JS = """
(() => {
    const isVisible = (el) => {
        if (!el.getBoundingClientRect) return false;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return (
            rect.width > 3 &&
            rect.height > 3 &&
            style.visibility !== 'hidden' &&
            style.display !== 'none' &&
            style.opacity !== '0' &&
            rect.bottom > 0 &&
            rect.top < window.innerHeight &&
            rect.right > 0 &&
            rect.left < window.innerWidth
        );
    };

    const interactiveSelectors = [
        'a[href]', 'button', 'input', 'select', 'textarea',
        '[role="button"]', '[role="link"]', '[role="checkbox"]',
        '[role="menuitem"]', '[role="tab"]', '[onclick]', '[tabindex]:not([tabindex="-1"])'
    ];

    const elements = Array.from(document.querySelectorAll(interactiveSelectors.join(',')))
        .filter(isVisible);

    let id = 1;
    return elements.map(el => {
        const rect = el.getBoundingClientRect();
        const role = el.getAttribute('role') || el.tagName.toLowerCase();
        let text = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || el.title || '').trim();
        text = text.replace(/\\s+/g, ' ').slice(0, 80);

        return {
            id: id++,
            tag: el.tagName.toLowerCase(),
            role: role,
            text: text,
            is_input: ['input', 'textarea', 'select'].includes(el.tagName.toLowerCase()),
            bounding_box: {
                x: rect.left,
                y: rect.top,
                width: rect.width,
                height: rect.height
            }
        };
    });
})()
"""

class PerceptionEngine:
    def __init__(self, draw_som: bool = False, max_image_width: int = 1280):
        self.draw_som = draw_som # Set-of-Marks numbered bounding boxes
        self.max_image_width = max_image_width

    async def capture(self, page: Page, vision_mode: bool = True) -> PerceptionState:
        url = page.url
        title = await page.title()
        
        # Scroll details
        scroll_info = await page.evaluate("""() => ({
            scroll_x: window.scrollX,
            scroll_y: window.scrollY,
            max_scroll_y: document.documentElement.scrollHeight - window.innerHeight
        })""")

        # Extract visible text summary (first 1500 chars)
        text_content = await page.evaluate("""() => {
            const body = document.body;
            if (!body) return '';
            return body.innerText.slice(0, 1500).replace(/\\n{2,}/g, '\\n').trim();
        }""")

        raw_elements: List[Dict[str, Any]] = await page.evaluate(EXTRACT_ELEMENTS_JS)
        elements = [
            DOMElement(
                id=item["id"],
                tag=item["tag"],
                role=item["role"],
                text=item["text"],
                selector=f"element-{item['id']}",
                bounding_box=item["bounding_box"],
                is_input=item["is_input"]
            )
            for item in raw_elements
        ]

        screenshot_bytes: Optional[bytes] = None
        if vision_mode:
            raw_png = await page.screenshot(type="png", full_page=False)
            if self.draw_som:
                screenshot_bytes = self._draw_set_of_marks(raw_png, elements)
            else:
                screenshot_bytes = raw_png

        return PerceptionState(
            url=url,
            title=title,
            scroll_x=scroll_info["scroll_x"],
            scroll_y=scroll_info["scroll_y"],
            max_scroll_y=scroll_info["max_scroll_y"],
            text_content=text_content,
            elements=elements,
            screenshot_bytes=screenshot_bytes
        )

    def _draw_set_of_marks(self, png_bytes: bytes, elements: List[DOMElement]) -> bytes:
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        draw = ImageDraw.Draw(img)

        for el in elements:
            box = el.bounding_box
            x, y, w, h = box["x"], box["y"], box["width"], box["height"]
            draw.rectangle([x, y, x + w, y + h], outline="#FF2E54", width=2)
            
            # Draw mark label badge
            badge_text = str(el.id)
            draw.rectangle([x, max(0, y - 16), x + 20, max(16, y)], fill="#FF2E54")
            draw.text((x + 3, max(0, y - 16)), badge_text, fill="white")

        output = io.BytesIO()
        img.save(output, format="JPEG", quality=85)
        return output.getvalue()
