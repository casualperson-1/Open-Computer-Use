from dataclasses import dataclass, field
from typing import List, Dict, Optional

@dataclass
class DOMElement:
    id: int
    tag: str
    role: str
    text: str
    selector: str
    bounding_box: Dict[str, float] # x, y, width, height
    is_clickable: bool = True
    is_input: bool = False
    attributes: Dict[str, str] = field(default_factory=dict)

@dataclass
class PerceptionState:
    url: str
    title: str
    scroll_x: float
    scroll_y: float
    max_scroll_y: float
    text_content: str
    elements: List[DOMElement]
    screenshot_bytes: Optional[bytes] = None # Vision mode
