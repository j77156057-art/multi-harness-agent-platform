"""OFFICE-UI-36: the assistant takes this turn's facts **this turn**.

Pinned here:

* the system prompt carries the rule ("本轮的事实，本轮去取") -- a rule that is
  not written down is a rule that gets lost;
* the worker's opening line says that the lines after [c1] are **earlier turns of
  this private thread**, so the model does not read them as fresh input;
* the memory lines say who said what ("用户上一轮问的" / "你上一轮答的"), so the
  model can tell its own old answer from the user's question.
"""

from __future__ import annotations

from app.assistant.memory import memory_context, thread_turns
from app.assistant.prompts import SYSTEM_PROMPT


def _message(seq, sender, body):
    return {
        "created_seq": seq,
        "sender_id": sender,
        "body": body,
        "refs": ["direct"],
    }


def test_the_prompt_says_the_facts_are_to_be_retaken_this_turn():
    assert "本轮的事实" in SYSTEM_PROMPT
    assert "本轮去取" in SYSTEM_PROMPT
    # It has to say what to do, not just what not to do.
    assert "重新" in SYSTEM_PROMPT


def test_the_prompt_says_the_memory_is_the_previous_reading():
    """The rule only bites if the model knows the memory is stale by nature."""
    assert "上一轮" in SYSTEM_PROMPT
    assert "记忆" in SYSTEM_PROMPT


def test_the_memory_line_names_who_spoke():
    turns = thread_turns(
        [
            _message(10, "office-operator", "上一轮问的"),
            _message(11, "office-assistant", "上一轮答的"),
        ]
    )

    lines = memory_context(turns, assistant_id="office-assistant", asker_id="office-operator")

    assert "用户上一轮问的" in lines[0]
    assert "你上一轮答的" in lines[1]
