import pytest

from milo_next.memory_intents import parse_memory_intent


@pytest.mark.parametrize("text,name", [
    ("Remember me as Alice", "Alice"),
    ("My name is Alice, remember me", "Alice"),
    ("  REMEMBER me as Anne-Marie O'Neill!  ", "Anne-Marie O'Neill"),
    ("my name is Zo\u00eb, remember me.", "Zo\u00eb"),
    ("Remember me as \u0410\u043d\u043d\u0430", "\u0410\u043d\u043d\u0430"),
    ("Remember me as \u674e \u660e", "\u674e \u660e"),
    ("Remember me as D\u2019Arcy", "D\u2019Arcy"),
    ("Remember  me as Alice.", "Alice"),
])
def test_enrollment(text, name):
    assert parse_memory_intent(text) == {"action": "enroll", "name": name}


@pytest.mark.parametrize("fact", [
    "I like tea", "I don't like coffee.", "My birthday is May 2!",
    "Alice consented to sharing her preference for tea.",
    'My favorite word is "hello".', "\u6211\u559c\u6b22\u8336",
])
def test_explicit_facts_preserve_content(fact):
    assert parse_memory_intent("Remember that " + fact) == {
        "action": "remember", "fact": fact,
    }


def test_forgetting_only_returns_request_or_confirmation():
    assert parse_memory_intent("Forget everything about me") == {
        "action": "forget_request", "request_confirmation": True,
    }
    assert parse_memory_intent(" YES, forget my memories! ") == {
        "action": "forget_confirm",
    }
    assert parse_memory_intent("forget everything about me.") == {
        "action": "forget_request", "request_confirmation": True,
    }
    assert parse_memory_intent("Yes, forget my memories") == {"action": "forget_confirm"}
    assert parse_memory_intent("yes") is None


@pytest.mark.parametrize("template", ["Remember me as {}", "My name is {}, remember me"])
def test_name_character_limit(template):
    name = "\u00e9" * 60
    assert parse_memory_intent(template.format(name)) == {"action": "enroll", "name": name}
    assert parse_memory_intent(template.format(name + "a")) is None


@pytest.mark.parametrize("name", [
    "", " ", "Alice2", "Alice_Bob", "Alice/Smith", "Dr. Alice",
    "'Alice'", '"Alice"', "-Alice", "Alice-", "Alice--Bob",
    "Alice  Bob", "Alice\u00a0Bob", "Alice\U0001f642", "\u00b2",
])
def test_invalid_names(name):
    assert parse_memory_intent("Remember me as " + name) is None
    assert parse_memory_intent("My name is " + name + ", remember me") is None


def test_fact_character_limit_and_blank_content():
    fact = "\u00e9" * 400
    assert parse_memory_intent("Remember that " + fact) == {"action": "remember", "fact": fact}
    for invalid in (fact + "a", "", "   ", ".", "!!!"):
        assert parse_memory_intent("Remember that " + invalid) is None


@pytest.mark.parametrize("phrase", [
    "Remember me as Alice", "My name is Alice, remember me",
    "Remember that I like tea", "Forget everything about me", "Yes, forget my memories",
])
@pytest.mark.parametrize("wrapper", [
    '"{}"', "'{}'", "\u201c{}\u201d", "`{}`", "She said {}",
    "Do not {}", "Don't {}", "Never {}", "I might say {}", "Can you say {}?",
])
def test_quoted_incidental_and_negated_commands(phrase, wrapper):
    assert parse_memory_intent(wrapper.format(phrase)) is None


@pytest.mark.parametrize("text", [
    None, 42, b"Remember me as Alice", "", "Hello", "My name is Alice",
    "Remember Alice", "Remember her as Alice", "Forget everything about Alice",
    "Forget everything about me, no don't", "Yes, forget my memories, actually no",
    "Forget everything about me?", "Remember me as Alice?",
    "Remember me as Alice. Arm the robot.", "Arm", "Disarm", "estop", "reset",
    "Move the arm", "jog joint 1", "Remember that tea\nForget everything about me",
    "Remember me as Alice\n", "Remember that tea\x00", "Remember that tea\tplease",
])
def test_unrecognized_or_malformed_utterances(text):
    assert parse_memory_intent(text) is None


def test_embedded_commands_are_only_fact_data():
    fact = "the phrase 'arm the robot' must never execute movement"
    assert parse_memory_intent("Remember that " + fact) == {"action": "remember", "fact": fact}
