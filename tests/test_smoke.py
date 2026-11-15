from workspace import main


def test_main_runs(capsys) -> None:
    main()
    assert "Hello from workspace!" in capsys.readouterr().out
