from pathlib import Path

from streamlit.testing.v1 import AppTest


UI_PATH = Path(__file__).parents[1] / "ui.py"


def _check_ui(condition: bool, success_message: str, error_message: str) -> None:
    if not condition:
        print(f"---FAIL----: {error_message}")
    assert condition, error_message
    print(f"---PASS----: {success_message}")


def test_dashboard_heading_is_displayed():
    app = AppTest.from_file(UI_PATH).run(timeout=15)

    _check_ui(not app.exception, "Dashboard ran without an exception.", "Dashboard raised an exception.")
    _check_ui(
        any("VNSP Intelligent Troubleshooter" in element.value for element in app.markdown),
        "UI heading is visible.",
        "Expected dashboard heading was not rendered.",
    )


def test_incident_intake_form_is_displayed():
    app = AppTest.from_file(UI_PATH).run(timeout=15)
    input_labels = {widget.label for widget in app.text_input}
    input_labels.update(widget.label for widget in app.text_area)
    input_labels.update(widget.label for widget in app.selectbox)
    button_labels = {button.label for button in app.button}

    _check_ui(
        {"Application Name", "Action Name", "Full Error Log Message"} <= input_labels
        and "Analyze incident" in button_labels,
        "Incident intake fields and Analyze incident button are visible.",
        "Incident intake form is missing an expected field or button.",
    )


def test_empty_incident_submission_shows_warning():
    app = AppTest.from_file(UI_PATH).run(timeout=15)
    app.button[0].click().run(timeout=15)

    _check_ui(
        any(
            "Enter an action name or a full error log message to analyze."
            in warning.value
            for warning in app.warning
        ),
        "Empty submission displays the required-input warning.",
        "Empty submission did not display the required-input warning.",
    )


def test_action_submission_displays_initial_assessment():
    app = AppTest.from_file(UI_PATH).run(timeout=15)
    app.text_input[0].set_value("CreateSubscription")
    app.button[0].click().run(timeout=15)
    rendered_text = [element.value for element in app.markdown]

    _check_ui(
        any("Initial assessment" in text for text in rendered_text)
        and any("Suggested checks" in text for text in rendered_text)
        and any("CreateSubscription" in text for text in rendered_text),
        "Action-only submission displays its summary, assessment, and suggested checks.",
        "Action-only submission did not display its summary and assessment.",
    )


def test_log_submission_displays_initial_assessment():
    app = AppTest.from_file(UI_PATH).run(timeout=15)
    log_message = "Connection timed out while creating subscription"
    app.text_area[0].set_value(log_message)
    app.button[0].click().run(timeout=15)
    rendered_text = [element.value for element in app.markdown]

    _check_ui(
        any("Initial assessment" in text for text in rendered_text)
        and any("Suggested checks" in text for text in rendered_text)
        and any(log_message in element.value for element in app.code),
        "Log-only submission displays an assessment, suggested checks, and submitted log.",
        "Log-only submission did not display its assessment or submitted log.",
    )


def test_default_application_is_selected():
    app = AppTest.from_file(UI_PATH).run(timeout=15)

    _check_ui(
        bool(app.selectbox[0].value),
        "An application is selected by default.",
        "No application is selected by default.",
    )


def test_environment_options_are_available():
    app = AppTest.from_file(UI_PATH).run(timeout=15)
    environment = next(widget for widget in app.selectbox if widget.label == "Environment")

    _check_ui(
        "Development" in environment.options and "Production" in environment.options,
        "Development and Production environment options are available.",
        "Expected environment options are missing.",
    )


def test_advanced_options_are_displayed():
    app = AppTest.from_file(UI_PATH).run(timeout=15)

    _check_ui(
        any(expander.label == "Advanced options" for expander in app.expander),
        "Advanced options are displayed.",
        "Advanced options are missing.",
    )


def test_retry_api_connection_button_is_displayed():
    app = AppTest.from_file(UI_PATH).run(timeout=15)

    _check_ui(
        any(button.label == "Retry API connection" for button in app.button),
        "Retry API connection button is displayed.",
        "Retry API connection button is missing.",
    )



def test_action_submission_displays_application_summary():
    app = AppTest.from_file(UI_PATH).run(timeout=15)
    app.text_input[0].set_value("CreateSubscription")
    app.button[0].click().run(timeout=15)

    _check_ui(
        any("**Application:**" in element.value for element in app.markdown),
        "Action submission displays the application summary.",
        "Action submission did not display the application summary.",
    )

    assert not app.exception, "Dashboard raised an exception."
    assert any(
        "VNSP Intelligent Troubleshooter" in element.value
        for element in app.markdown
    ), "Dashboard heading is missing."

    input_labels = {widget.label for widget in app.text_input}
    input_labels.update(widget.label for widget in app.text_area)
    input_labels.update(widget.label for widget in app.selectbox)
    assert {
        "Application Name",
        "Action Name",
        "Full Error Log Message",
        "Environment",
    } <= input_labels, "An expected incident input field is missing."
    assert any(
        button.label == "Retry API connection" for button in app.button
    ), "Retry API connection button is missing."

    app.selectbox[0].select("VNSP")
    app.text_input[0].set_value("CreateSubscription")
    log_message = "Connection timed out while creating subscription"
    app.text_area[0].set_value(log_message)
    app.text_input[1].set_value("subscription-api")
    app.selectbox[1].select("Production")
    app.text_input[2].set_value("req-12345")
    app.text_input[3].set_value("2026-10-10 14:30 UTC")
    app.button[0].click().run(timeout=15)

    assert not app.exception, "Incident analysis raised an exception."
    rendered_text = [element.value for element in app.markdown]
    rendered_captions = [element.value for element in app.caption]
    summary = " ".join(rendered_text + rendered_captions)

    assert any("Initial assessment" in text for text in rendered_text), (
        "Initial assessment is missing."
    )
    assert any("Suggested checks" in text for text in rendered_text), (
        "Suggested checks are missing."
    )
    assert all(
        value in summary
        for value in (
            "VNSP",
            "CreateSubscription",
            "subscription-api",
            "Production",
            "req-12345",
            "2026-10-10 14:30 UTC",
        )
    ), "Incident details are missing from the summary."
    assert any(log_message in element.value for element in app.code), (
        "Submitted log is missing from the log expander."
    )

def test_whitespace_only_submission_shows_warning():
    app = AppTest.from_file(UI_PATH).run(timeout=15)

    app.text_input[0].set_value("   ")
    app.text_area[0].set_value("   ")

    analyze_button = next(
        button for button in app.button if button.label == "Analyze incident"
    )
    analyze_button.click().run(timeout=15)

    assert any(
        "Enter an action name or a full error log message to analyze."
        in warning.value
        for warning in app.warning
    )
    # To run the test cases:
    # python -m pytest -v -s tests\test_ui_smoke.py