"""Shared test constants used across test modules."""

CSV_HEADER = [
    "Status", "Company", "Product/Purpose", "Role", "Comp", "Location",
    "Fit", "GD Rating", "Source", "Date Found",
    "Date Applied", "Link", "Notes",
]

SAMPLE_CONFIG = {
    "output_dir": "~/jobs",
    "job_search": {
        "roles": ["SRE", "Platform Engineer", "DevOps Engineer"],
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
                "anthropic": False,
            },
        },
    },
}
