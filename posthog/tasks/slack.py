import re

import requests
from celery import shared_task
from django.apps import apps
from django.conf import settings


def get_user_details(event, site_url: str) -> (str, str):
    try:
        user_name = event.person.properties.get("email")
    except:
        user_name = event.distinct_id

    if get_webhook_type(event.team) == "slack":
        user_markdown = "<{}/person/{}|{}>".format(
            site_url, event.distinct_id, user_name,
        )
    else:
        user_markdown = "[{}]({}/person/{})".format(
            user_name, site_url, event.distinct_id,
        )
    return user_name, user_markdown


def get_action_details(action, event, site_url: str) -> (str, str):
    if get_webhook_type(event.team) == "slack":
        action_markdown = '"<{}/action/{}|{}>"'.format(site_url, action.id, action.name)
    else:
        action_markdown = '"[{}]({}/action/{})"'.format(
            action.name, site_url, action.id,
        )
    return action.name, action_markdown


def get_tokens(message_format: str) -> (str, str):
    matched_tokens = re.findall(r"(?<=\[)(.*?)(?=\])", message_format)
    if matched_tokens:
        tokenised_message = re.sub(r"\[(.*?)\]", "{}", message_format)
        return matched_tokens, tokenised_message
    raise ValueError


def get_value_of_token(action, event, site_url: str, token_parts: list) -> (str, str):
    if token_parts[0] == "user":
        if token_parts[1] == "name":
            user_name, user_markdown = get_user_details(event, site_url)
            return user_name, user_markdown
        else:
            user_property = event.properties.get("$" + token_parts[1])
            if user_property is None:
                raise ValueError
            return user_property, user_property

    elif token_parts[0] == "action":
        if token_parts[1] == "name":
            action_name, action_markdown = get_action_details(action, event, site_url)
            return action_name, action_markdown

    elif token_parts[0] == "event":
        if token_parts[1] == "name":
            return event.event, event.event


def get_formatted_message(action, event, site_url: str) -> (str, str):
    message_format = action.slack_message_format
    if message_format is None:
        message_format = "[action.name] was triggered by [user.name]"

    tokens, tokenised_message = get_tokens(message_format)
    values = []
    markdown_values = []

    try:
        for token in tokens:
            token_parts = re.findall(r"\w+", token)

            value, markdown_value = get_value_of_token(
                action, event, site_url, token_parts,
            )
            values.append(value)
            markdown_values.append(markdown_value)

        message_text = tokenised_message.format(*values)
        message_markdown = tokenised_message.format(*markdown_values)

    except ValueError:
        action_name, action_markdown = get_action_details(action, event, site_url)
        error_message = "⚠ Error: There are one or more formatting errors in the message template for action {}."
        message_text = error_message.format('"' + action.name + '"')
        message_markdown = "*" + error_message.format(action_markdown) + "*"

    return message_text, message_markdown


def get_webhook_type(team) -> str:
    if "slack.com" in team.slack_incoming_webhook:
        return "slack"
    return "teams"


@shared_task
def post_event_to_slack(event_id: int, site_url: str) -> None:
    # must import "Event" like this to avoid circular dependency with models/event.py (it imports tasks/slack.py)
    event_model = apps.get_model(app_label="posthog", model_name="Event")
    event = event_model.objects.get(pk=event_id)
    team = event.team
    actions = [action for action in event.action_set.all() if action.post_to_slack]

    if not site_url:
        site_url = settings.SITE_URL

    if team.slack_incoming_webhook and actions:
        for action in actions:
            message_text, message_markdown = get_formatted_message(
                action, event, site_url,
            )
            if get_webhook_type(team) == "slack":
                message = {
                    "text": message_text,
                    "blocks": [
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": message_markdown},
                        },
                    ],
                }
            else:
                message = {
                    "text": message_markdown,
                }
            requests.post(team.slack_incoming_webhook, verify=False, json=message)
