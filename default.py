# -*- coding: utf-8 -*-
"""SubDL subtitle provider for Kodi 18+ compatible with local CLI testing.

This implementation keeps the Kodi 18/Python 2 compatibility style while also
working under Python 3 for local validation. It is designed to satisfy the
project requirements around search, filtering, logging, config, download
validation, and filesystem safety.

Author: JCA, 2026, Rev: 1.09
"""

from __future__ import print_function

import datetime
import json
import os
import posixpath
import re
import sys
import unicodedata
import zipfile

try:  # Kodi 18 / Python 2.7
    import urllib
    import urllib2
    import urlparse
    from StringIO import StringIO as BytesIO
except ImportError:  # Local Python 3 testing
    import urllib.parse as urllib
    import urllib.request as urllib2
    import urllib.parse as urlparse
    from io import BytesIO

try:
    text_type = unicode
except NameError:
    text_type = str

try:
    import xbmc
    import xbmcaddon
    import xbmcgui
    import xbmcplugin
    import xbmcvfs
    KODI_AVAILABLE = True
except ImportError:
    KODI_AVAILABLE = False

    class MockAddon(object):
        def getSetting(self, key):
            if key == "sub_api_key":
                return os.environ.get("SUBDL_API_KEY", "")
            if key == "sub_langs":
                return os.environ.get("SUBDL_LANGS", "PTBR")
            if key == "sub_download_path":
                return os.environ.get("SUBDL_DOWNLOAD_PATH", "")
            if key == "sub_log_level":
                return os.environ.get("SUB_LOG_LEVEL", "INFO")
            return ""

        def getAddonInfo(self, key):
            if key == 'path':
                return os.path.dirname(os.path.abspath(__file__))
            if key == 'profile':
                return os.path.dirname(os.path.abspath(__file__))
            if key == 'id':
                return "service.subsearch"
            return ""

    class MockXBMC(object):
        def translatePath(self, path):
            return path

        class Player(object):
            def getPlayingFile(self):
                return ""

    class MockGui(object):
        NOTIFICATION_INFO = 0

        class Dialog(object):
            def notification(self, *args, **kwargs):
                pass

        class ListItem(object):
            def __init__(self, *args, **kwargs):
                self.path = kwargs.get("path", "")

            def setArt(self, *args, **kwargs):
                pass

    class MockVFS(object):
        def translatePath(self, path):
            return path

    xbmc = MockXBMC()
    xbmcgui = MockGui()
    xbmcplugin = type('MockPlugin', (object,), {
        'endOfDirectory': lambda *a, **k: None,
        'addDirectoryItem': lambda *a, **k: True,
        'setResolvedUrl': lambda *a, **k: None,
    })()
    xbmcaddon = type('MockAddonModule', (object,), {'Addon': lambda: MockAddon()})()
    xbmcvfs = MockVFS()

CLI_MODE = not KODI_AVAILABLE

# Add-on initialization.
try:
    ADDON = xbmcaddon.Addon()
    ADDON_PATH = ADDON.getAddonInfo('path')
    ADDON_ID = ADDON.getAddonInfo('id') or "service.subsearch"
    _profile = ADDON.getAddonInfo('profile')
    if hasattr(xbmc, 'translatePath'):
        PROFILE_DIR = xbmc.translatePath(_profile)
    else:
        PROFILE_DIR = xbmcvfs.translatePath(_profile)
    if isinstance(PROFILE_DIR, bytes):
        PROFILE_DIR = PROFILE_DIR.decode('utf-8', 'replace')
except Exception:
    ADDON = None
    ADDON_PATH = os.path.dirname(os.path.abspath(__file__))
    ADDON_ID = "service.subsearch"
    PROFILE_DIR = ADDON_PATH

DEFAULT_LOGFILE = os.path.join(PROFILE_DIR, "subsearch.log") if KODI_AVAILABLE else "/storage/.kodi/userdata/addon_data/service.subsearch/subsearch.log"
LOGFILE = os.environ.get("SUBSEARCH_LOGFILE", DEFAULT_LOGFILE)
SUBDL_API_URL = "https://api.subdl.com/api/v1/subtitles"
SUBDL_DOWNLOAD_BASE_URL = "https://api.subdl.com"


ALLOWED_DOWNLOAD_HOSTS = ("api.subdl.com", "dl.subdl.com")

MAX_FILENAME_LENGTH = 255
MAX_SUBTITLES_LIMIT = 100
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_SEARCH_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_SRT_BYTES = 20 * 1024 * 1024
HTTP_TIMEOUT = 15

def safe_str(value):
    if isinstance(value, text_type):
        return value
    if isinstance(value, bytes):
        try:
            return value.decode('utf-8', 'replace')
        except Exception:
            return value.decode('latin-1', 'replace')
    try:
        return text_type(value)
    except Exception:
        return u"<unprintable>"


def _safe_display_text(value, limit=200):
    text = re.sub(u'[\x00-\x1f\x7f]', u' ', safe_str(value))
    return re.sub(u'\\s+', u' ', text).strip()[:limit]


def _utf8_bytes(value):
    value = safe_str(value)
    if isinstance(value, text_type):
        return value.encode('utf-8')
    return value


def _urlencode(params):
    encoded = {}
    for key, value in params.items():
        if isinstance(value, text_type):
            encoded[key] = _utf8_bytes(value)
        else:
            encoded[key] = value
    return urllib.urlencode(encoded)


def get_setting(setting_id, default_value=u""):
    if ADDON:
        try:
            value = ADDON.getSetting(setting_id)
            if value is not None and safe_str(value).strip():
                return safe_str(value).strip()
        except Exception:
            pass
    return default_value


def get_configured_apikey():
    value = get_setting("sub_api_key", os.environ.get("SUBDL_API_KEY", ""))
    return safe_str(value).strip()


def get_configured_languages():
    value = get_setting("sub_langs", os.environ.get("SUBDL_LANGS", "PTBR"))
    return safe_str(value).strip()


def get_configured_log_level():
    value = get_setting("sub_log_level", os.environ.get("SUB_LOG_LEVEL", "INFO"))
    value = safe_str(value).strip().upper()
    return "DEBUG" if value in ("DEBUG", "1") else "INFO"


def get_configured_download_dir(current_video_path=None):
    raw_path = get_setting("sub_download_path", os.environ.get("SUBDL_DOWNLOAD_PATH", ""))
    if raw_path:
        try:
            translated = xbmc.translatePath(raw_path) if hasattr(xbmc, 'translatePath') else xbmcvfs.translatePath(raw_path)
            candidate = safe_str(translated).strip()
            if candidate:
                if _is_vfs_path(candidate):
                    if KODI_AVAILABLE and _path_exists(candidate):
                        return candidate.rstrip('/')
                    log("Configured VFS subtitle directory is unavailable: '{0}'".format(candidate))
                    candidate = ""
                if not candidate:
                    raise ValueError("Configured VFS directory is unavailable")
                candidate = os.path.realpath(candidate)
                if _is_safe_local_dir(candidate):
                    return candidate
                log("Configured subtitle directory rejected as unsafe: '{0}'".format(candidate))
        except Exception:
            pass

    if current_video_path:
        video_path = safe_str(current_video_path)
        if video_path:
            if _is_vfs_path(video_path):
                candidate = posixpath.dirname(video_path)
                if KODI_AVAILABLE and candidate and _path_exists(candidate):
                    return candidate
            else:
                candidate = os.path.dirname(video_path)
                if candidate:
                    candidate = os.path.realpath(candidate)
                    if _is_safe_local_dir(candidate):
                        return candidate

    if KODI_AVAILABLE:
        try:
            playing = xbmc.Player().getPlayingFile()
            if playing:
                playing = safe_str(playing)
                if _is_vfs_path(playing):
                    candidate = posixpath.dirname(playing)
                    if _path_exists(candidate):
                        return candidate
                else:
                    candidate = os.path.realpath(os.path.dirname(playing))
                    if _is_safe_local_dir(candidate):
                        return candidate
        except Exception:
            pass

    fallback = os.path.realpath(os.path.join(PROFILE_DIR, "downloads"))
    if not os.path.exists(fallback):
        try:
            _make_dirs(fallback)
        except Exception:
            fallback = os.path.realpath("/storage/downloads/subs")
            if not os.path.exists(fallback):
                try:
                    _make_dirs(fallback)
                except Exception:
                    fallback = os.path.realpath(os.path.join(PROFILE_DIR, "downloads"))
    return fallback


def _is_vfs_path(path):
    path = safe_str(path).lower()
    return path.startswith(("smb://", "nfs://", "dav://", "davs://", "ftp://", "ftps://", "special://"))


def _has_symlink_component(path):
    normalized = os.path.abspath(path)
    drive, tail = os.path.splitdrive(normalized)
    current = drive + os.sep if tail.startswith(os.sep) else drive
    for component in tail.split(os.sep):
        if not component:
            continue
        current = os.path.join(current, component) if current else component
        if os.path.islink(current):
            return True
    return False


def _is_safe_local_dir(path):
    if not path or _is_vfs_path(path):
        return False
    normalized = os.path.abspath(path)
    if _has_symlink_component(normalized):
        log("CRITICAL: Rejected symbolic link in local subtitle destination '{0}'.".format(
            _safe_display_text(path, 300)))
        return False
    if not os.path.isdir(normalized):
        try:
            _make_dirs(normalized)
        except Exception:
            return False
    return os.path.isdir(normalized) and not _has_symlink_component(normalized)


def _path_join(directory, filename):
    if _is_vfs_path(directory):
        return posixpath.join(directory.rstrip('/'), filename)
    return os.path.join(directory, filename)


def _path_exists(path):
    try:
        if _is_vfs_path(path):
            return KODI_AVAILABLE and hasattr(xbmcvfs, 'exists') and xbmcvfs.exists(path)
        if KODI_AVAILABLE and hasattr(xbmcvfs, 'exists'):
            return xbmcvfs.exists(path)
        return os.path.exists(path)
    except Exception:
        return False


def _make_dirs(path):
    if _is_vfs_path(path):
        if KODI_AVAILABLE and hasattr(xbmcvfs, 'mkdirs'):
            return xbmcvfs.mkdirs(path)
        raise ValueError("Kodi VFS is required to create a network download directory")
    if KODI_AVAILABLE and hasattr(xbmcvfs, 'mkdirs'):
        return xbmcvfs.mkdirs(path)
    if not os.path.exists(path):
        os.makedirs(path)
    return True


def _write_bytes(path, data):
    if KODI_AVAILABLE and hasattr(xbmcvfs, 'File'):
        if _path_exists(path):
            log("Overwriting existing subtitle file '{0}'.".format(
                _safe_display_text(path, 300)))
        handle = xbmcvfs.File(path, 'wb')
        try:
            write_result = handle.write(data)
            if write_result is False:
                raise IOError("Kodi VFS reported that the file write failed")
        finally:
            try:
                handle.close()
            except Exception:
                pass
        if not _path_exists(path):
            raise IOError("Kodi VFS did not create the written file")
        return
    if os.path.islink(path) or _has_symlink_component(os.path.dirname(path) or os.curdir):
        log("CRITICAL: Rejected symbolic link in subtitle output path '{0}'.".format(
            _safe_display_text(path, 300)))
        raise IOError("Refusing to write through a symbolic link")
    if os.path.exists(path):
        log("Overwriting existing subtitle file '{0}'.".format(
            _safe_display_text(path, 300)))
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    flags |= getattr(os, 'O_NOFOLLOW', 0)
    file_descriptor = os.open(path, flags, 0o666)
    with os.fdopen(file_descriptor, 'wb') as output:
        output.write(data)


def redact_sensitive_data(message):
    text = safe_str(message)
    api_key = get_configured_apikey()
    if api_key:
        text = text.replace(api_key, u"[REDACTED]")
    for key_name in ("api_key", "sub_api_key", "apiKey"):
        text = re.sub(
            r"(?i)(%s(?:\s*[:=]\s*['\"]?))"
            r"(?!%%5BREDACTED%%5D|\[REDACTED\])[^'\"\s,&?#}]+"
            % key_name,
            r"\1[REDACTED]",
            text,
        )
    return text


def _redact_url(url):
    parsed = urlparse.urlparse(safe_str(url))
    sensitive_names = (
        "api_key", "apikey", "key", "token", "access_token",
        "auth", "signature", "sig"
    )
    query = []
    for key, value in urlparse.parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in sensitive_names:
            value = "[REDACTED]"
        query.append((key, value))
    netloc = parsed.netloc.rsplit("@", 1)[-1]
    return urlparse.urlunparse((
        parsed.scheme, netloc, parsed.path, parsed.params,
        urllib.urlencode(query), parsed.fragment
    ))


def _debug_safe_api_value(value, key=""):
    key_name = safe_str(key).lower()
    sensitive_names = (
        "api_key", "apikey", "key", "access_token", "token", "secret",
        "password", "authorization", "signature", "auth"
    )
    if key_name in sensitive_names or key_name.endswith(
            ("_key", "_token", "_secret", "_password")):
        return "[REDACTED]"
    if isinstance(value, dict):
        return dict((safe_str(item_key), _debug_safe_api_value(item_value, item_key))
                    for item_key, item_value in value.items())
    if isinstance(value, list):
        return [_debug_safe_api_value(item) for item in value]
    if isinstance(value, bytes):
        value = safe_str(value)
    if isinstance(value, text_type) and (
            "url" in key_name or re.match(r"(?i)^(?:https?://|/)", value)):
        return _redact_url(value)
    return value


def log(message):
    try:
        message = redact_sensitive_data(message)
        if (CLI_MODE and get_configured_log_level() == "DEBUG" and
                message.startswith("DEBUG: ")):
            sys.stdout.write(message + "\n")
            sys.stdout.flush()
        log_dir = os.path.dirname(LOGFILE)
        if log_dir and not os.path.isdir(log_dir):
            _make_dirs(log_dir)
        if not os.path.isdir(PROFILE_DIR):
            _make_dirs(PROFILE_DIR)
        formatted = u"[{0}] {1}\n".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), message)
        with open(LOGFILE, "ab") as output:
            output.write(formatted.encode('utf-8', 'replace'))
            output.flush()
    except Exception as error:
        try:
            sys.stderr.write("LOG_FAILURE: {0}\n".format(safe_str(error).encode('utf-8', 'replace')))
        except Exception:
            pass


def debug_log(message):
    if get_configured_log_level() == "DEBUG":
        log("DEBUG: {0}".format(safe_str(message)))


def show_notification(message, title="SubDL Search"):
    if KODI_AVAILABLE:
        try:
            xbmcgui.Dialog().notification(title, safe_str(message), xbmcgui.NOTIFICATION_INFO, 3000)
        except Exception:
            pass
    else:
        try:
            print(u"[NOTIFY] {0}: {1}".format(title, safe_str(message)))
        except Exception:
            pass


def sanitize_filename(filename):
    filename = safe_str(filename)
    filename = filename.replace('\\', '/')
    filename = filename.rsplit('/', 1)[-1]
    filename = re.sub(u'[\r\n\t\x00-\x1f]', u' ', filename)
    filename = re.sub(u'\\\\s+', u' ', filename).strip()
    filename = filename.strip(' .')
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    filename = filename.strip(' .')
    if not filename or filename in (u'.', u'..'):
        filename = u"subtitle"
    return filename[:MAX_FILENAME_LENGTH]


def sanitize_languages(languages):
    cleaned_languages = []
    for language in re.split(r'[|,]', safe_str(languages)):
        cleaned = re.sub(r'[^A-Za-z._-]', '', language.strip())
        if cleaned:
            cleaned_languages.append(cleaned)
    return '|'.join(cleaned_languages)


def normalize_languages(languages):
    return sanitize_languages(languages).replace('|', ',')


def extract_season_episode(filename):
    """Recognize common SxxExx, Season xx Episode xx, and xxXxx patterns."""
    text = safe_str(filename)
    patterns = (
        r'(?i)(?:^|[^a-z0-9])s(\d{1,2})[\s._-]*e(\d{1,2})(?:[^0-9]|$)',
        r'(?i)(?:^|[^a-z0-9])season[\s._-]*(\d{1,2})[\s._-]*(?:episode|ep)[\s._-]*(\d{1,2})(?:[^0-9]|$)',
        r'(?i)(?:^|[^0-9])(\d{1,2})x(\d{1,2})(?:[^0-9]|$)'
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None, None


def build_query_subdl(filename, languages):
    basename = safe_str(filename).replace('\\', '/')
    basename = basename.rsplit('/', 1)[-1]
    season, episode = extract_season_episode(basename)
    query = {
        "file_name": sanitize_filename(basename),
        "languages": normalize_languages(languages)
    }
    if season is not None and episode is not None:
        query['season_number'] = season
        query['episode_number'] = episode
    return query


def _http_read(url, limit=None):
    response = urllib2.urlopen(url, timeout=HTTP_TIMEOUT)
    try:
        if limit is None:
            return response.read()
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Response exceeds the configured size limit")
        return data
    finally:
        try:
            response.close()
        except Exception:
            pass


def _log_api_response(data):
    if not isinstance(data, dict):
        message = "SubDL API response is not a JSON object (type={0}).".format(
            type(data).__name__)
        if get_configured_log_level() == "DEBUG":
            debug_log(message)
        else:
            log(message)
        return
    raw = data.get("subtitles", [])
    if get_configured_log_level() != "DEBUG":
        log("SubDL API response: status={0}, candidate_count={1}.".format(
            safe_str(data.get("status", "unspecified")),
            len(raw) if isinstance(raw, list) else "unavailable"))
        return
    debug_log("SubDL API response: status={0}, candidate_count={1}, top_level_keys={2}.".format(
        safe_str(data.get("status", "unspecified")),
        len(raw) if isinstance(raw, list) else "unavailable",
        ", ".join(sorted(safe_str(key) for key in data.keys()))))
    metadata = dict((key, value) for key, value in data.items() if key != "subtitles")
    debug_log("SubDL API response metadata: {0}".format(
        json.dumps(_debug_safe_api_value(metadata), sort_keys=True, ensure_ascii=True)))
    if not isinstance(raw, list):
        debug_log("SubDL API response subtitles field is not a list (type={0}).".format(
            type(raw).__name__))
        return
    for ordinal, subtitle in enumerate(raw, 1):
        if not isinstance(subtitle, dict):
            debug_log("SubDL API candidate {0} is not an object (type={1}).".format(
                ordinal, type(subtitle).__name__))
            continue
        debug_log("SubDL API candidate {0}: {1}".format(
            ordinal, json.dumps(_debug_safe_api_value(subtitle),
                                sort_keys=True, ensure_ascii=True)))


def subdl_search(query):
    api_key = get_configured_apikey()
    if not api_key:
        log("ERROR: SubDL API key is not configured.")
        show_notification("Please configure your SubDL API key in add-on settings.")
        return {"_search_error": "SubDL API key is not configured"}

    params = {
        "api_key": api_key,
        "file_name": query.get("file_name", ""),
        "languages": query.get("languages", ""),
        "subs_per_page": MAX_SUBTITLES_LIMIT
    }
    if "season_number" in query:
        params["season_number"] = query["season_number"]
    if "episode_number" in query:
        params["episode_number"] = query["episode_number"]

    url = SUBDL_API_URL + "?" + _urlencode(params)
    if get_configured_log_level() == "DEBUG":
        debug_log("SubDL API request: GET {0}".format(_redact_url(url)))
    else:
        log("Searching SubDL: file='{0}', languages='{1}', season='{2}', episode='{3}'".format(
            query.get('file_name', ''),
            query.get('languages', ''),
            query.get('season_number', ''),
            query.get('episode_number', '')))

    try:
        payload = _http_read(url, MAX_SEARCH_RESPONSE_BYTES)
        try:
            data = json.loads(payload)
            _log_api_response(data)
            if not isinstance(data, dict):
                return {"_search_error": "SubDL API response was not a JSON object"}
            return data
        except Exception as error:
            message = "SubDL API response could not be decoded as JSON: {0}".format(
                _safe_display_text(error, 300))
            log(message)
            return {"_search_error": message}
    except urllib2.HTTPError as error:
        if error.code == 429:
            log("SEARCH_EXCEPTION: HTTP 429 rate limit exceeded.")
            show_notification("SubDL API rate limit reached. Please wait and try again.")
            return {"_search_error": "SubDL API rate limit exceeded (HTTP 429)"}
        else:
            log("SEARCH_EXCEPTION: HTTP {0}.".format(error.code))
            return {"_search_error": "SubDL API returned HTTP {0}".format(error.code)}
    except Exception as error:
        log("SEARCH_EXCEPTION: {0}".format(safe_str(error)))
        return {"_search_error": _safe_display_text(
            redact_sensitive_data(error), 500)}


def _candidate_file_extension(subtitle):
    extensions = []
    for value in (subtitle.get("name", ""), subtitle.get("url", "")):
        value = safe_str(value).strip()
        if not value:
            continue
        path = urlparse.urlparse(value).path
        extension = os.path.splitext(path)[1].lower()
        if extension:
            extensions.append(extension)
    if not extensions or len(set(extensions)) != 1:
        return ""
    extension = extensions[0]
    return extension if extension in (".srt", ".zip") else ""


def _candidate_name(subtitle):
    name = safe_str(subtitle.get("name", "")).strip()
    if not name:
        name = os.path.basename(urlparse.urlparse(
            safe_str(subtitle.get("url", ""))).path)
    return name or "subtitle.srt"


def _subtitle_candidates(data):
    raw = data.get("subtitles", []) if isinstance(data, dict) else []
    if not isinstance(raw, list):
        return []
    raw = raw[:MAX_SUBTITLES_LIMIT]
    candidates = []
    for subtitle in raw:
        if not isinstance(subtitle, dict):
            log("Rejected SubDL result: candidate is not an object.")
            continue
        candidate_name = _candidate_name(subtitle)
        if not safe_str(subtitle.get("url", "")).strip():
            log("Rejected SubDL candidate '{0}': missing download URL.".format(
                _safe_display_text(candidate_name, 200)))
            continue
        if not _candidate_file_extension(subtitle):
            log("Rejected SubDL candidate '{0}': only .srt and .zip files are supported.".format(
                _safe_display_text(candidate_name, 200)))
            continue
        debug_log("Candidate accepted for selection: name='{0}', extension='{1}'.".format(
            _safe_display_text(candidate_name, 200),
            _candidate_file_extension(subtitle)))
        candidates.append(subtitle)
    return candidates


def _unique_output_path(directory, filename):
    is_vfs = _is_vfs_path(directory)
    base_dir = directory.rstrip('/') if is_vfs else os.path.realpath(directory)
    if not _path_exists(base_dir):
        _make_dirs(base_dir)
    candidate_name = sanitize_filename(filename)
    return _path_join(base_dir, candidate_name)


def _safe_archive_member_name(member_name):
    name = safe_str(member_name).replace('\\', '/')
    name = name.rsplit('/', 1)[-1]
    if not name or name in (u'.', u'..'):
        return None
    name = re.sub(u'[\x00-\x1f<>:"/\\|?*]', u'_', name)
    name = name.strip(' .')
    if not name or name in (u'.', u'..'):
        return None
    if not name.lower().endswith('.srt'):
        return None
    return name[:MAX_FILENAME_LENGTH]


def _is_within_directory(base_dir, target_path):
    if _is_vfs_path(base_dir) or _is_vfs_path(target_path):
        if not (_is_vfs_path(base_dir) and _is_vfs_path(target_path)):
            return False
        base_url = urlparse.urlparse(safe_str(base_dir))
        target_url = urlparse.urlparse(safe_str(target_path))
        if (base_url.scheme.lower(), base_url.netloc.lower()) != (
                target_url.scheme.lower(), target_url.netloc.lower()):
            return False
        base_path = posixpath.normpath(base_url.path.rstrip('/'))
        target_path = posixpath.normpath(target_url.path)
        return target_path == base_path or target_path.startswith(base_path.rstrip('/') + '/')
    base_dir = os.path.realpath(base_dir)
    target_path = os.path.realpath(target_path)
    try:
        return target_path == base_dir or target_path.startswith(base_dir + os.sep)
    except Exception:
        return False


def extract_srt_files(archive_data, download_dir, language=""):
    extracted = []
    archive = None
    try:
        if not zipfile.is_zipfile(BytesIO(archive_data)):
            raise ValueError("Downloaded file is not a valid ZIP archive")
        archive = zipfile.ZipFile(BytesIO(archive_data), 'r')
        total_size = 0
        for member in archive.infolist():
            if member.filename.endswith(('/', '\\')):
                continue
            filename = _safe_archive_member_name(member.filename)
            if not filename:
                log("Rejected archive member '{0}': unsafe or unsupported filename.".format(
                    _safe_display_text(member.filename, 200)))
                continue
            if member.flag_bits & 0x1:
                log("Rejected archive member '{0}': encrypted.".format(filename))
                continue
            mode = (member.external_attr >> 16) & 0xFFFF
            if (mode & 0o170000) == 0o120000:
                log("CRITICAL: Rejected symbolic link in subtitle archive: '{0}'.".format(
                    _safe_display_text(member.filename, 200)))
                continue
            if member.file_size <= 0 or member.file_size > MAX_SRT_BYTES:
                log("Rejected archive member '{0}': empty or oversized ({1} bytes).".format(
                    filename, member.file_size))
                continue
            total_size += member.file_size
            if total_size > MAX_ARCHIVE_BYTES:
                log("Rejected archive member '{0}': cumulative subtitle size limit exceeded.".format(
                    filename))
                continue
            try:
                content = archive.read(member)
            except Exception as error:
                log("Rejected archive member '{0}': could not read ({1}).".format(
                    filename, _safe_display_text(error, 200)))
                continue
            target = _unique_output_path(download_dir, filename)
            if not _is_within_directory(download_dir, target):
                log("Rejected archive member '{0}': destination is outside the download directory.".format(
                    filename))
                continue
            _write_bytes(target, content)
            extracted.append(target)
            debug_log("Extracted validated subtitle file to '{0}' ({1} bytes).".format(
                _safe_display_text(target, 500), len(content)))
    except Exception as error:
        log("EXTRACT_EXCEPTION: {0}".format(safe_str(error)))
    finally:
        if archive:
            try:
                archive.close()
            except Exception:
                pass
    return extracted


def _validated_download_url(subtitle_url):
    subtitle_url = safe_str(subtitle_url).strip()
    if not subtitle_url:
        raise ValueError("Subtitle download URL is empty")
    subtitle_url = urlparse.urljoin(SUBDL_DOWNLOAD_BASE_URL + '/', subtitle_url)
    parsed = urlparse.urlparse(subtitle_url)
    if not parsed.netloc:
        raise ValueError("Subtitle download URL is missing host")
    if parsed.scheme != 'https':
        raise ValueError("Unsupported subtitle download scheme")
    hostname = parsed.hostname.lower() if parsed.hostname else ""
    if hostname not in ALLOWED_DOWNLOAD_HOSTS:
        raise ValueError("Subtitle download URL is not an approved SubDL host")
    path = parsed.path or ""
    if '../' in path or '..\\' in path or '%2e%2e' in path.lower():
        raise ValueError("Path traversal in subtitle URL")
    filename = os.path.basename(path.replace('\\', '/'))
    extension = os.path.splitext(safe_str(filename))[1].lower()
    if extension and extension not in ('.zip', '.srt'):
        raise ValueError("Unsupported download file extension: {0}".format(extension))
    return subtitle_url


def _url_file_extension(subtitle_url):
    path = urlparse.urlparse(subtitle_url).path or ''
    return os.path.splitext(path)[1].lower()


def _looks_like_srt(data):
    try:
        text = data.decode('utf-8-sig')
    except (AttributeError, UnicodeDecodeError):
        try:
            text = data.decode('utf-16')
        except (AttributeError, UnicodeDecodeError):
            return False
    return re.search(
        r'(?m)^\s*\d{1,6}\s*\r?\n\s*\d{2}:\d{2}:\d{2}[,.]\d{3}\s+-->\s+\d{2}:\d{2}:\d{2}[,.]\d{3}',
        text) is not None


def download_and_get_path(subtitle_url, subtitle_name=None, current_video_path=None,
                          language=""):
    download_dir = get_configured_download_dir(current_video_path)
    debug_log("Resolved download destination: video='{0}', directory='{1}', "
              "log_level='{2}'.".format(
                  _safe_display_text(current_video_path or "", 500),
                  _safe_display_text(download_dir, 500),
                  get_configured_log_level()))
    if not _path_exists(download_dir):
        try:
            _make_dirs(download_dir)
        except Exception as error:
            log("WARNING: Could not create download directory: {0}".format(safe_str(error)))
            download_dir = os.path.realpath(os.path.join(PROFILE_DIR, "downloads"))
            if not _path_exists(download_dir):
                _make_dirs(download_dir)

    if not _path_exists(download_dir):
        raise ValueError("Subtitle download directory does not exist and could not be created")

    download_url = _validated_download_url(subtitle_url)
    log("Downloading selected SubDL subtitle to '{0}'.".format(download_dir))
    debug_log("SubDL download request: GET {0}; destination='{1}'.".format(
        _redact_url(download_url), _safe_display_text(download_dir, 500)))

    try:
        data = _http_read(download_url, MAX_ARCHIVE_BYTES)
    except Exception as error:
        log("DOWNLOAD_EXCEPTION: {0}".format(safe_str(error)))
        raise
    debug_log("Downloaded {0} bytes into memory for content validation.".format(len(data)))

    url_extension = _url_file_extension(download_url)
    candidate_name = safe_str(subtitle_name or os.path.basename(urlparse.urlparse(download_url).path))
    candidate_extension = os.path.splitext(candidate_name)[1].lower()
    is_zip = zipfile.is_zipfile(BytesIO(data))
    is_srt = _looks_like_srt(data)
    debug_log("Downloaded content checks: url_extension='{0}', candidate_extension='{1}', "
              "valid_zip={2}, valid_srt={3}.".format(
                  url_extension, candidate_extension, is_zip, is_srt))

    if url_extension and url_extension not in ('.zip', '.srt'):
        debug_log("Download rejected: URL has unsupported extension '{0}'.".format(
            url_extension))
        raise ValueError("Unsupported downloaded file extension: {0}".format(url_extension))
    if url_extension == '.zip' and not is_zip:
        debug_log("Download rejected: URL indicates ZIP but content is not a valid archive.")
        raise ValueError("Downloaded content is not a valid ZIP archive")
    if url_extension == '.srt' and not is_srt:
        debug_log("Download rejected: URL indicates SRT but content is not a valid subtitle.")
        raise ValueError("Downloaded content is not a valid SRT subtitle")
    if candidate_extension and candidate_extension not in ('.zip', '.srt'):
        debug_log("Download rejected: candidate has unsupported extension '{0}'.".format(
            candidate_extension))
        raise ValueError("Unsupported subtitle candidate extension: {0}".format(candidate_extension))
    if candidate_extension == '.zip' and not is_zip:
        debug_log("Download rejected: selected ZIP candidate did not contain a valid archive.")
        raise ValueError("Selected .zip candidate did not contain a valid ZIP archive")
    if candidate_extension == '.srt' and not is_srt:
        debug_log("Download rejected: selected SRT candidate did not contain a valid subtitle.")
        raise ValueError("Selected .srt candidate did not contain a valid SRT subtitle")

    if is_srt and not is_zip:
        source_name = candidate_name
        if os.path.splitext(source_name)[1].lower() != '.srt':
            source_name = os.path.splitext(source_name)[0] + '.srt'
        safe_name = sanitize_filename(source_name)
        target = _unique_output_path(download_dir, safe_name)
        if not _is_within_directory(download_dir, target):
            debug_log("SRT save rejected: destination is outside the configured download directory.")
            raise ValueError("Destination path escapes configured directory")
        _write_bytes(target, data)
        log("Saved validated SRT subtitle to '{0}'.".format(
            _safe_display_text(target, 500)))
        debug_log("Saved validated SRT subtitle ({0} bytes).".format(len(data)))
        return target

    if is_zip:
        if get_configured_log_level() == "DEBUG":
            archive_name = candidate_name
            if os.path.splitext(archive_name)[1].lower() != '.zip':
                archive_name = os.path.splitext(archive_name)[0] + '.zip'
            archive_name = sanitize_filename(archive_name)
            archive_path = _unique_output_path(download_dir, archive_name)
            if not _is_within_directory(download_dir, archive_path):
                debug_log("ZIP save rejected: destination is outside the configured download directory.")
                raise ValueError("Archive destination escapes configured directory")
            debug_log("Saving validated ZIP for inspection to '{0}'.".format(
                _safe_display_text(archive_path, 500)))
            _write_bytes(archive_path, data)
            if not _path_exists(archive_path):
                raise IOError("Validated ZIP archive was not found after saving")
            log("Saved validated subtitle archive to '{0}'.".format(
                _safe_display_text(archive_path, 500)))
            debug_log("Saved validated ZIP archive ({0} bytes).".format(len(data)))
        log("Extracting subtitles from validated archive.")
        debug_log("Extracting validated SRT members from the downloaded archive.")
        extracted_files = extract_srt_files(data, download_dir, language)
        if not extracted_files:
            log("No usable SRT subtitles were extracted from the archive.")
            debug_log("No archive members passed subtitle extraction checks; no file returned.")
            show_notification("The downloaded archive did not contain a usable SRT subtitle.")
            return None
        log("Extracted {0} usable subtitle file(s) from the archive.".format(
            len(extracted_files)))
        debug_log("Archive extraction completed with {0} validated subtitle file(s).".format(
            len(extracted_files)))
        return extracted_files[0]

    raise ValueError("Only .srt and .zip files are allowed for download")


def _get_handle():
    try:
        if len(sys.argv) > 1:
            return int(sys.argv[1])
    except (TypeError, ValueError):
        pass
    return -1


def _end_directory(handle):
    if handle != -1:
        try:
            xbmcplugin.endOfDirectory(handle)
        except Exception as error:
            log("Could not finish Kodi directory response: {0}".format(safe_str(error)))


def handle_manual_search(args_string):
    parts = args_string.split(';')
    filename = parts[1] if len(parts) > 1 else ""
    languages = parts[2] if len(parts) > 2 else get_configured_languages()
    target_season, target_episode = extract_season_episode(filename)
    query = build_query_subdl(filename, languages)
    log("Manual search: file='{0}', languages='{1}', episode={2}x{3}.".format(
        query.get('file_name', ''), query.get('languages', ''), target_season, target_episode))

    if not KODI_AVAILABLE:
        print("CLI Manual Search -> file_name='{0}' languages='{1}' "
              "season='{2}' episode='{3}'".format(
                  query["file_name"], query["languages"],
                  query.get("season_number", ""), query.get("episode_number", "")))

    data = subdl_search(query)
    if data.get("_search_error"):
        message = "SubDL search failed: {0}".format(data["_search_error"])
        log(message)
        if not KODI_AVAILABLE:
            print(message)
        return

    raw_candidates = data.get("subtitles", [])
    raw_candidate_count = len(raw_candidates) if isinstance(raw_candidates, list) else 0
    subtitles = _subtitle_candidates(data)
    log("Manual search returned {0} candidate(s).".format(len(subtitles)))
    if not subtitles:
        if raw_candidate_count:
            message = (
                "SubDL returned {0} candidate(s), but none passed the local "
                ".srt/.zip download checks. See the Subsearch log for details."
            ).format(raw_candidate_count)
        else:
            message = "SubDL returned no subtitle candidates for '{0}'.".format(
                query["file_name"])
        log(message)
        if not KODI_AVAILABLE:
            print(message)
        return

    selectable = []
    for ordinal, subtitle in enumerate(subtitles, 1):
        language = _safe_display_text(subtitle.get("language", ""))
        subtitle_name = _safe_display_text(_candidate_name(subtitle))
        subtitle_url = safe_str(subtitle.get("url", ""))
        log("Candidate {0}: language='{1}', name='{2}'".format(
            ordinal, language or "Unknown", subtitle_name))
        if not KODI_AVAILABLE:
            print("{0}) [{1}] {2}".format(
                ordinal, language or "Unknown language", subtitle_name))
        if subtitle_url:
            selectable.append((ordinal, subtitle))

    if not KODI_AVAILABLE:
        print("")
        if not selectable:
            print("No downloadable candidates were returned.")
            return
        try:
            selection = _read_cli_input("Enter candidate number to download (0 to cancel): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("Download selection cancelled.")
            log("CLI download selection cancelled (no input).")
            return

        if not selection.isdigit():
            print("Invalid selection; enter one of the listed candidate numbers.")
            log("CLI download selection rejected: non-numeric input.")
            return

        selected_ordinal = int(selection)
        if selected_ordinal == 0:
            print("Download cancelled.")
            log("CLI download selection cancelled by user.")
            return

        selected = next(
            (subtitle for ordinal, subtitle in selectable if ordinal == selected_ordinal),
            None)
        if selected is None:
            print("Invalid selection; enter one of the listed candidate numbers.")
            log("CLI download selection rejected: ordinal {0} is unavailable.".format(selected_ordinal))
            return

        selected_url = safe_str(selected.get("url", ""))
        selected_name = _candidate_name(selected)
        debug_log("CLI selected candidate {0}: name='{1}', language='{2}', url={3}.".format(
            selected_ordinal, _safe_display_text(selected_name, 300),
            _safe_display_text(selected.get("language", ""), 64),
            _redact_url(selected_url)))
        try:
            downloaded_path = download_and_get_path(
                selected_url,
                selected_name,
                current_video_path=filename,
                language=safe_str(selected.get("language", "")))
        except Exception as error:
            log("CLI download failed for candidate {0}: {1}".format(
                selected_ordinal, safe_str(error)))
            debug_log("CLI selected candidate download did not complete: {0}".format(
                _safe_display_text(error, 500)))
            print("Download failed. Check the Subsearch log for details.")
            return

        if downloaded_path:
            log("CLI download succeeded for candidate {0}: '{1}'.".format(
                selected_ordinal,
                posixpath.basename(downloaded_path) if _is_vfs_path(downloaded_path)
                else os.path.basename(downloaded_path)))
            print("Downloaded subtitle: {0}".format(downloaded_path))
        else:
            debug_log("CLI selected candidate download returned no usable subtitle path.")
            log("CLI download produced no usable subtitle for candidate {0}.".format(
                selected_ordinal))
            print("No usable subtitle was downloaded. Check the Subsearch log for details.")


def _read_cli_input(prompt):
    try:
        read_input = raw_input
    except NameError:
        read_input = input
    return read_input(prompt)


def handle_kodi_ui_search():
    handle = _get_handle()
    try:
        try:
            filename = xbmc.Player().getPlayingFile()
        except Exception:
            filename = ""
        if not filename:
            log("No active playing video file detected.")
            return

        query = build_query_subdl(filename, get_configured_languages())
        target_season, target_episode = extract_season_episode(filename)
        log("Kodi search: file='{0}', languages='{1}', episode={2}x{3}.".format(
            query.get('file_name', ''), query.get('languages', ''), target_season, target_episode))
        data = subdl_search(query)
        subtitles = _subtitle_candidates(data)
        if not data.get("status", False) or not subtitles or handle == -1:
            debug_log("Kodi search will not offer downloads: status={0}, usable_candidates={1}, "
                      "directory_handle={2}.".format(
                          safe_str(data.get("status", False)), len(subtitles), handle))
            log("No matching subtitles found, or Kodi supplied an invalid directory handle.")
            return

        for ordinal, subtitle in enumerate(subtitles, 1):
            subtitle_url = safe_str(subtitle.get("url", ""))
            subtitle_name = _candidate_name(subtitle)
            language = safe_str(subtitle.get("language", "EN"))
            release = safe_str(subtitle.get("release_name", subtitle_name))
            if not subtitle_url:
                debug_log("Kodi candidate {0} not offered: missing download URL.".format(ordinal))
                continue
            debug_log("Kodi candidate {0} offered: name='{1}', release='{2}', language='{3}', "
                      "url={4}.".format(
                          ordinal, _safe_display_text(subtitle_name, 300),
                          _safe_display_text(release, 300),
                          _safe_display_text(language, 64), _redact_url(subtitle_url)))
            item = xbmcgui.ListItem(label=language, label2=release)
            item.setArt({'icon': '0', 'thumb': language.lower()})
            plugin_url = "plugin://{0}/?{1}".format(ADDON_ID, _urlencode({
                "action": "download",
                "url": subtitle_url,
                "name": subtitle_name,
                "language": language
            }))
            xbmcplugin.addDirectoryItem(handle=handle, url=plugin_url, listitem=item, isFolder=False)
        debug_log("Kodi search results offered; waiting for the user to select a candidate.")
    except Exception as error:
        log("Kodi UI search failed: {0}".format(safe_str(error)))
        show_notification("Subtitle search failed. Check the Kodi log for details.")
    finally:
        _end_directory(handle)


def _parse_query_string(paramstring):
    parsed = urlparse.parse_qs(paramstring.lstrip('?'), keep_blank_values=True)
    result = {}
    for key, values in parsed.items():
        result[key] = values[0] if values else ""
    return result


def _return_subtitle_to_kodi(handle, path=None):
    if handle == -1:
        return
    try:
        if path:
            item = xbmcgui.ListItem(label=path)
            added = xbmcplugin.addDirectoryItem(
                handle=handle, url=path, listitem=item, isFolder=False)
            if not added:
                log("Kodi did not accept the downloaded subtitle path.")
    except Exception as error:
        log("Could not return subtitle to Kodi: {0}".format(safe_str(error)))
    finally:
        _end_directory(handle)


def handle_kodi_ui_download(paramstring):
    params = _parse_query_string(paramstring)
    subtitle_url = params.get("url", "")
    debug_log("Kodi selected candidate: name='{0}', language='{1}', url={2}.".format(
        _safe_display_text(params.get("name", ""), 300),
        _safe_display_text(params.get("language", ""), 64),
        _redact_url(subtitle_url) if subtitle_url else "<missing>"))
    current_video_path = ""
    try:
        current_video_path = xbmc.Player().getPlayingFile() if KODI_AVAILABLE else ""
    except Exception:
        pass

    handle = _get_handle()
    if not subtitle_url:
        debug_log("Download not started: selected candidate did not include a URL.")
        log("Download request rejected: missing subtitle URL.")
        show_notification("The selected subtitle has no download URL.")
        _return_subtitle_to_kodi(handle)
        return

    try:
        subtitle_path = download_and_get_path(
            subtitle_url,
            params.get("name", ""),
            current_video_path=current_video_path,
            language=params.get("language", ""))
        if subtitle_path and _path_exists(subtitle_path):
            log("Subtitle downloaded and resolved successfully.")
            _return_subtitle_to_kodi(handle, subtitle_path)
        else:
            show_notification("Subtitle download failed. Check the Kodi log.")
            _return_subtitle_to_kodi(handle)
    except Exception as error:
        log("Download failed: {0}".format(safe_str(error)))
        debug_log("Selected candidate download did not complete: {0}".format(
            _safe_display_text(error, 500)))
        show_notification("Subtitle download failed. Check the Kodi log.")
        _return_subtitle_to_kodi(handle)


if __name__ == '__main__':
    CLI_MODE = any(";" in arg for arg in sys.argv[1:])
    if "action=download" in " ".join(sys.argv):
        download_arg = next((arg for arg in sys.argv if "action=download" in arg), "")
        handle_kodi_ui_download(download_arg)
    elif any(";" in arg for arg in sys.argv[1:]):
        cli_arg = next((arg for arg in sys.argv[1:] if ";" in arg), "")
        handle_manual_search(cli_arg)
    else:
        handle_kodi_ui_search()
