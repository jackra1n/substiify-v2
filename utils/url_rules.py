from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlsplit, urlunsplit

import aiohttp

logger = logging.getLogger(__name__)

CLEARURLS_RULES_URL = "https://raw.githubusercontent.com/ClearURLs/Rules/master/data.min.json"
RULES_CACHE_PATH = Path("cache/url_rules_cache.json")
RULES_CACHE_MAX_AGE = timedelta(hours=24)


@dataclass(slots=True)
class CompiledProvider:
	name: str
	url_pattern: re.Pattern[str]
	rules: tuple[re.Pattern[str], ...]
	referral_marketing: tuple[re.Pattern[str], ...]
	raw_rules: tuple[re.Pattern[str], ...]
	exceptions: tuple[re.Pattern[str], ...]
	redirections: tuple[re.Pattern[str], ...]
	force_redirection: bool


def _compile_patterns(patterns: Any) -> tuple[re.Pattern[str], ...]:
	if not isinstance(patterns, list):
		return ()

	compiled_patterns: list[re.Pattern[str]] = []
	for pattern in patterns:
		if not isinstance(pattern, str):
			continue

		try:
			compiled_patterns.append(re.compile(pattern, re.IGNORECASE))
		except re.error:
			continue

	return tuple(compiled_patterns)


def _validate_payload(payload: Any) -> dict[str, Any]:
	if not isinstance(payload, dict) or not isinstance(payload.get("providers"), dict):
		raise ValueError("Rules payload must contain a providers object")
	return payload


def compile_rules(payload: dict[str, Any]) -> list[CompiledProvider]:
	providers_payload = _validate_payload(payload)["providers"]
	compiled_providers: list[CompiledProvider] = []

	for name, provider in providers_payload.items():
		if not isinstance(provider, dict):
			continue

		url_pattern = provider.get("urlPattern")
		if not isinstance(url_pattern, str):
			continue

		try:
			compiled_url_pattern = re.compile(url_pattern, re.IGNORECASE)
		except re.error:
			continue

		compiled_providers.append(
			CompiledProvider(
				name=name,
				url_pattern=compiled_url_pattern,
				rules=_compile_patterns(provider.get("rules")),
				referral_marketing=_compile_patterns(provider.get("referralMarketing")),
				raw_rules=_compile_patterns(provider.get("rawRules")),
				exceptions=_compile_patterns(provider.get("exceptions")),
				redirections=_compile_patterns(provider.get("redirections")),
				force_redirection=bool(provider.get("forceRedirection", False)),
			)
		)

	if not compiled_providers:
		raise ValueError("No valid URL cleaning providers found")

	return compiled_providers


def is_cache_fresh() -> bool:
	if not RULES_CACHE_PATH.exists():
		return False

	age_seconds = max(0.0, time.time() - RULES_CACHE_PATH.stat().st_mtime)
	return age_seconds <= RULES_CACHE_MAX_AGE.total_seconds()


def read_cached_rules() -> dict[str, Any] | None:
	if not RULES_CACHE_PATH.exists():
		return None

	try:
		payload = json.loads(RULES_CACHE_PATH.read_text(encoding="utf-8"))
		return _validate_payload(payload)
	except (OSError, json.JSONDecodeError, ValueError) as exc:
		logger.warning(f"Failed to read cached URL rules: {exc}")
		return None


def write_cached_rules(payload: dict[str, Any]) -> None:
	validated_payload = _validate_payload(payload)
	RULES_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
	RULES_CACHE_PATH.write_text(json.dumps(validated_payload), encoding="utf-8")


async def fetch_rules_payload(session: aiohttp.ClientSession | None = None) -> dict[str, Any]:
	async def _fetch(active_session: aiohttp.ClientSession) -> dict[str, Any]:
		async with active_session.get(CLEARURLS_RULES_URL) as response:
			response.raise_for_status()
			return _validate_payload(await response.json(content_type=None))

	if session is not None:
		return await _fetch(session)

	async with aiohttp.ClientSession() as transient_session:
		return await _fetch(transient_session)


async def load_compiled_rules(session: aiohttp.ClientSession | None = None) -> list[CompiledProvider]:
	if is_cache_fresh():
		cached_payload = read_cached_rules()
		if cached_payload is not None:
			return compile_rules(cached_payload)

	try:
		payload = await fetch_rules_payload(session)
	except Exception:
		cached_payload = read_cached_rules()
		if cached_payload is not None:
			logger.warning("Using stale cached URL rules after fetch failure")
			return compile_rules(cached_payload)
		raise

	write_cached_rules(payload)
	return compile_rules(payload)


async def refresh_compiled_rules(session: aiohttp.ClientSession | None = None) -> list[CompiledProvider]:
	payload = await fetch_rules_payload(session)
	write_cached_rules(payload)
	return compile_rules(payload)


class URLRulesCleaner:
	def __init__(self, providers: list[CompiledProvider]):
		self.providers = providers
		self.url_pattern = re.compile(r"(https?://[^\s<]+[^<.,:;\"'>)\]\s])")

	def replace_url(self, url: str) -> tuple[str, list[str], bool]:
		try:
			urlsplit(url)
		except ValueError:
			return url, [], False

		current_url = url
		removed_trackers: list[str] = []
		was_redirected = False

		for provider in self.providers:
			if not provider.url_pattern.search(current_url):
				continue

			if any(exception.search(current_url) for exception in provider.exceptions):
				continue

			redirected_url = self._apply_redirections(current_url, provider)
			if redirected_url is not None and redirected_url != current_url:
				current_url = redirected_url
				was_redirected = True
				if not provider.force_redirection:
					continue

			current_url, provider_removed = self._remove_tracking_from_url(current_url, provider)
			removed_trackers.extend(provider_removed)

		return current_url, removed_trackers, was_redirected

	def clean_message_urls(self, message: str) -> tuple[list[str], list[str]]:
		cleaned_urls: list[str] = []
		removed_trackers: list[str] = []

		def process_url(match: re.Match[str]) -> str:
			cleaned_url, removed, _ = self.replace_url(match.group(0))
			cleaned_urls.append(cleaned_url)
			removed_trackers.extend(removed)
			return cleaned_url

		self.url_pattern.sub(process_url, message)
		return cleaned_urls, removed_trackers

	def _apply_redirections(self, url: str, provider: CompiledProvider) -> str | None:
		for redirection in provider.redirections:
			match = redirection.search(url)
			if match is None:
				continue

			for group in match.groups():
				candidate = self._normalize_redirect_target(group)
				if candidate is not None:
					return candidate

		return None

	def _normalize_redirect_target(self, candidate: str | None) -> str | None:
		if not candidate:
			return None

		decoded_candidate = candidate
		for _ in range(3):
			new_candidate = unquote(decoded_candidate)
			if new_candidate == decoded_candidate:
				break
			decoded_candidate = new_candidate

		decoded_candidate = decoded_candidate.strip()
		try:
			parsed_candidate = urlparse(decoded_candidate)
		except ValueError:
			return None

		if parsed_candidate.scheme not in {"http", "https"} or not parsed_candidate.netloc:
			return None

		return decoded_candidate

	def _remove_tracking_from_url(self, url: str, provider: CompiledProvider) -> tuple[str, list[str]]:
		try:
			parsed_url = urlsplit(url)
		except ValueError:
			return url, []

		patterns = provider.rules + provider.referral_marketing
		removed_params: list[str] = []
		filtered_query: list[tuple[str, str]] = []

		for key, value in parse_qsl(parsed_url.query, keep_blank_values=True):
			if any(pattern.fullmatch(key) for pattern in patterns):
				removed_params.append(key)
				continue
			filtered_query.append((key, value))

		updated_url = url
		if removed_params:
			updated_url = urlunsplit(
				(
					parsed_url.scheme,
					parsed_url.netloc,
					parsed_url.path,
					urlencode(filtered_query, doseq=True),
					parsed_url.fragment,
				)
			)

		for raw_rule in provider.raw_rules:
			updated_url = raw_rule.sub("", updated_url)

		return updated_url, removed_params
