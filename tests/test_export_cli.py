import click


def test_export_lists_nested_commands_without_running_callbacks() -> None:
    from tools.export_cli import describe

    def forbidden() -> None:
        raise AssertionError("Export must not invoke any command callback")

    market = click.Command("market", callback=forbidden, help="Pokaż ceny.\n\nDalszy opis.", params=[click.Argument(["city"], required=False)])
    hidden = click.Command("secret", callback=forbidden, hidden=True)
    stats = click.Group("stats", callback=forbidden, commands={"secret": hidden, "market": market})
    show = click.Command("show", callback=forbidden, help="Pokaż skill.")
    app = click.Group(callback=forbidden, commands={"stats": stats, "skill": click.Group(commands={"show": show})})

    assert describe(app) == [
        {"name": "skill show", "usage": "deweloperuch skill show [OPTIONS]", "description": "Pokaż skill."},
        {"name": "stats market", "usage": "deweloperuch stats market [OPTIONS] [CITY]", "description": "Pokaż ceny."},
    ]
