"""
Mock property context.

In production this lives in Postgres (see schema.sql in Part 2). For the
assessment we keep it as a static dict keyed by property_id, so the AI prompt
gets a predictable, well-formed context block.
"""
from typing import Optional

PROPERTIES: dict[str, dict] = {
    "villa-b1": {
        "name": "Villa B1",
        "location": "Assagao, North Goa",
        "bedrooms": 3,
        "max_guests": 6,
        "private_pool": True,
        "check_in_time": "2:00 PM",
        "check_out_time": "11:00 AM",
        "base_rate_inr": 18000,
        "base_rate_covers_guests": 4,
        "extra_guest_rate_inr": 2000,
        "wifi_password": "Nistula@2024",
        "caretaker_hours": "8:00 AM to 10:00 PM",
        "chef_on_call": True,
        "chef_requires_pre_booking": True,
        "availability": {
            # mock availability window
            "2026-04-20_to_2026-04-24": True,
        },
        "cancellation_policy": "Free cancellation up to 7 days before check-in",
    },
}


def get_property_context(property_id: Optional[str]) -> Optional[dict]:
    """Return the property dict, or None if unknown."""
    if not property_id:
        return None
    return PROPERTIES.get(property_id)


def format_property_for_prompt(property_id: Optional[str]) -> str:
    """
    Render a property as a clean, line-by-line block for the Claude prompt.
    Returns a fallback notice if the property is unknown.
    """
    ctx = get_property_context(property_id)
    if not ctx:
        return "(No property context available - reply generically and flag for agent review.)"

    return (
        f"Property: {ctx['name']}, {ctx['location']}\n"
        f"Bedrooms: {ctx['bedrooms']} | Max guests: {ctx['max_guests']} | "
        f"Private pool: {'Yes' if ctx['private_pool'] else 'No'}\n"
        f"Check-in: {ctx['check_in_time']} | Check-out: {ctx['check_out_time']}\n"
        f"Base rate: INR {ctx['base_rate_inr']:,} per night "
        f"(up to {ctx['base_rate_covers_guests']} guests)\n"
        f"Extra guest: INR {ctx['extra_guest_rate_inr']:,} per night per person\n"
        f"WiFi password: {ctx['wifi_password']}\n"
        f"Caretaker: Available {ctx['caretaker_hours']}\n"
        f"Chef on call: {'Yes, pre-booking required' if ctx['chef_on_call'] else 'No'}\n"
        f"Availability April 20-24: Available\n"
        f"Cancellation: {ctx['cancellation_policy']}"
    )
