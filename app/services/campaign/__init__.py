"""Campaign domain (Phase D.14).

Authoritative marketing-campaign source domain. Campaigns are firm marketing assets; no
campaign data lives inside Opportunities. Opportunities REFERENCE a campaign (attribution);
Campaigns never own Opportunities. Campaign performance/ROI is computed from attributed
opportunities, never duplicated.
"""
