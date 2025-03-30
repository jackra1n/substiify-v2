import math

__all__ = ("seconds_to_human_readable", "bytes_to_human_readable")


def seconds_to_human_readable(seconds: int) -> str:
	if seconds <= 60:
		return "<1 minute"
	elif 3600 > seconds > 60:
		minutes = seconds // 60
		seconds = seconds % 60
		return f"{int(minutes)}m {int(seconds)}s"
	hours = seconds // 3600  # Seconds divided by 3600 gives amount of hours
	minutes = (seconds % 3600) // 60  # The remaining seconds are looked at to see how many minutes they make up
	if hours >= 24:
		days = hours // 24
		hours = hours % 24
		return f"{int(days)}d {int(hours)}h {int(minutes)}m"
	return f"{int(hours)}h {int(minutes)}m"


def bytes_to_human_readable(size_in_bytes: int) -> str:
	units = ("B", "KB", "MB", "GB", "TB")
	power = int(math.log(size_in_bytes, 1024))
	return f"{size_in_bytes / (1024**power):.1f} {units[power]}"
