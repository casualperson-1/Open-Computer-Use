import csv
import io
from datetime import datetime
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict

@dataclass
class Deal:
    title: str
    price: float
    url: str
    original_price: Optional[float] = None
    discount_pct: Optional[float] = None
    seller: Optional[str] = None
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat()
        if self.original_price and self.original_price > self.price and not self.discount_pct:
            self.discount_pct = round(((self.original_price - self.price) / self.original_price) * 100, 1)

class DealFinderEngine:
    def __init__(self):
        # In-memory or database-backed price history map: title/normalized_url -> list of Deal records
        self.price_history: Dict[str, List[Deal]] = {}

    def normalize_key(self, deal: Deal) -> str:
        return deal.title.lower().strip()

    def process_findings(self, new_deals: List[Deal]) -> Dict[str, Any]:
        """
        Deduplicate, detect real price drops against recorded history,
        rank by discount or price, and store history.
        """
        processed = []
        flagged_drops = []

        for d in new_deals:
            key = self.normalize_key(d)
            history = self.price_history.setdefault(key, [])

            is_drop = False
            drop_amount = 0.0
            if history:
                lowest_past = min(prev.price for prev in history)
                if d.price < lowest_past:
                    is_drop = True
                    drop_amount = round(lowest_past - d.price, 2)

            history.append(d)
            item_info = asdict(d)
            item_info["price_drop"] = is_drop
            item_info["savings"] = drop_amount
            processed.append(item_info)

            if is_drop:
                flagged_drops.append(item_info)

        # Sort by discount percentage descending, then price ascending
        processed.sort(key=lambda x: (-(x.get("discount_pct") or 0.0), x["price"]))

        return {
            "total_deals": len(processed),
            "flagged_drops": flagged_drops,
            "deals": processed
        }

    def export_csv(self, deals: List[Dict[str, Any]]) -> str:
        """Export deals list to CSV string."""
        output = io.StringIO()
        fieldnames = ["title", "price", "original_price", "discount_pct", "seller", "url", "price_drop", "savings", "timestamp"]
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for d in deals:
            writer.writerow(d)
        return output.getvalue()
