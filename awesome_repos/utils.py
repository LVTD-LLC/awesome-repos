import structlog


def get_awesome_repos_logger(name):
    """This will add a `awesome_repos` prefix to logger for easy configuration."""

    return structlog.get_logger(f"awesome_repos.{name}", project="awesome_repos")
