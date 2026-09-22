"""Coarse browser context for display only, never authenticator identity or trust."""


def passkey_client(user_agent: str | None) -> tuple[str | None, str | None]:
    ua = (user_agent or "")[:1024]
    browser = next(
        (
            name
            for token, name in (
                ("Edg/", "Edge"),
                ("EdgiOS/", "Edge"),
                ("EdgA/", "Edge"),
                ("OPR/", "Opera"),
                ("SamsungBrowser/", "Samsung Internet"),
                ("FxiOS/", "Firefox"),
                ("Firefox/", "Firefox"),
                ("CriOS/", "Chrome"),
                ("Chrome/", "Chrome"),
                ("Version/", "Safari"),
            )
            if token in ua
        ),
        None,
    )
    platform = next(
        (
            name
            for token, name in (
                ("iPhone", "iOS"),
                ("iPad", "iPadOS"),
                ("iPod", "iOS"),
                ("Android", "Android"),
                ("Windows", "Windows"),
                ("CrOS", "ChromeOS"),
                ("Macintosh", "macOS"),
                ("Mac OS X", "macOS"),
                ("Linux", "Linux"),
            )
            if token in ua
        ),
        None,
    )
    if browser == "Safari" and "Safari/" not in ua:
        browser = None
    return browser, platform
