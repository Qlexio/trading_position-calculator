from trading_position_calculator import main


def test_main_runs(capsys) -> None:
    main()
    assert "Hello from trading-position-calculator!" in capsys.readouterr().out
