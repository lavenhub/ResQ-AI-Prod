"""Backend service layer.

`emergency_reference` holds stable, authoritative reference data (official
helpline numbers). `retrieval` performs live, time-sensitive lookups (weather,
nearby hospitals, advisories) against external APIs with timeouts, caching and
honest failure handling. Neither is ever imported by frontend code.
"""
