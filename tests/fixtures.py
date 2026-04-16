"""Shared test constants used across test modules."""

CSV_HEADER = [
    "Status", "Notes", "Company", "Location", "Role", "Fit", "Comp",
    "Date Found", "Date Applied", "Link", "Product/Purpose",
    "GD Rating", "Source",
]

SAMPLE_CONFIG = {
    "output_dir": "~/jobs",
    "job_search": {
        "roles": ["SRE", "Platform Engineer", "DevOps Engineer"],
        "persona": "SRE/Platform/Infra engineer",
        "locations": {
            "home_city": "Vancouver, BC",
            "remote": True,
            "countries": ["US", "CA"],
            "cities": [],
        },
        "scraper": {
            "enabled": True,
            "user_agent": "TestAgent/1.0",
            "timeout": 5,
            "remoteok_max_jobs": 50,
            "hn_max_posts": 10,
            "sources": {
                "remoteok": True,
                "weworkremotely": True,
                "hn_who_is_hiring": False,
                "greenhouse": False,
            },
        },
    },
}
