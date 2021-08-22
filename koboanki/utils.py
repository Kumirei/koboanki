import requests
import sqlite3
import threading
from aqt import mw
from aqt.qt import QAction
from aqt.utils import showInfo, qconnect
from os import path
from queue import Queue
import json
from PyQt5.QtWidgets import QFileDialog

def get_config() -> dict:
    config = mw.addonManager.getConfig(__name__)

    # TODO: improve the config file verification
    if not config:
        showInfo("Config file is empty")
        return {}
    if not "language_list" in config:
        showInfo("Config file does not contain a language list")
        return {}
    if len(config["language_list"]) == 0:
        showInfo("Language list is empty")
        return {}

    links = {code: get_link(code, "test") for code in config["language_list"]}
    links_statuses = {code: try_link(link) for code, link in links.items()}
    failed_codes = [code for code, status in links_statuses.items() if not status]
    if failed_codes:
        showInfo(f"The following language codes are not valid: {failed_codes}")
        return {}

    return config

def verify_config(config) -> bool:
    # TODO
    return True


def get_blacklist() -> list:
    user_files_dir = path.join(mw.pm.addonFolder(), "koboanki", "user_files")
    with open(path.join(user_files_dir, "blacklist.json")) as file:
        blacklist = json.load(file)
    lower_blacklist = [word.lower() for word in blacklist]
    return lower_blacklist


def get_link(language_code: str, word: str) -> str:
    return f"https://api.dictionaryapi.dev/api/v2/entries/{language_code}/{word}"


def try_link(link) -> bool:
    "Attempts to connect to a given link. Used to verify internet connection and language codes"
    valid = True
    try:
        response = requests.get(link)
        if response.status_code == 404:
            valid = False
    except requests.exceptions.ConnectionError:
        valid = False

    return valid


def get_file_location() -> str:
    """Returns the kobo db file locatoin. Empty if error or not found."""
    folder_name = QFileDialog.getExistingDirectory(
        None, "Select KOBO drive", path.expanduser("~"), QFileDialog.ShowDirsOnly
    )

    if folder_name:
        file_location = path.join(folder_name, ".kobo", "KoboReader.sqlite")
        if not (path.exists(file_location) and path.isfile(file_location)):
            showInfo(f"File path not found: {file_location}")
            file_location = ""
    else:
        file_location = ""

    return file_location


def get_kobo_wordlist(file_location: str) -> list:
    """Opens the kobo file and returns a list of saved words."""
    connection = sqlite3.connect(file_location)
    cursor = connection.cursor()
    wordlist = [
        row[0] for row in cursor.execute("SELECT text from WordList").fetchall()
    ]
    lower_wordlist = [word.lower() for word in wordlist]
    return lower_wordlist


def get_new_wordlist(kobo_wordlist: list) -> list:
    """Returns a list of only words not already added to anki."""
    ids = mw.col.find_notes("")
    anki_wordlist = [mw.col.getNote(id_).items()[0][1] for id_ in ids]
    new_wordlist = [word for word in kobo_wordlist if word not in anki_wordlist]
    return new_wordlist


def get_definitions(wordlist: list, config: dict) -> tuple:
    """Concurently find defintions for all words"""
    queue = Queue(maxsize=0)
    num_theads = min(config["dl_threads"], len(wordlist))
    definitions = [{} for _ in wordlist]
    for i in range(len(wordlist)):
        queue.put((i, wordlist[i]))

    # create threads
    for i in range(num_theads):
        worker = threading.Thread(
            target=queue_handler, args=(queue, definitions, config)
        )
        worker.setDaemon(True)
        worker.start()
    queue.join()

    # seperate working and broken words
    definition_dict = {}
    failed_words = []
    for word, definition in zip(wordlist, definitions):
        if definition:
            definition_dict[word] = definition
        else:
            failed_words.append(word)

    return (definition_dict, failed_words)


def queue_handler(queue: Queue, definitions: list, config: dict) -> bool:
    """Threads are created pointing at this function to get the word defintions"""
    while not queue.empty():
        work = queue.get()
        word = work[1]

        definition = ""
        for language in config["language_list"]:
            definition = get_word_definition(
                word, language, config["dl_timeout"], config["dl_retries"]
            )
            if definition != "":
                break

        definitions[work[0]] = definition
        queue.task_done()
    return True


def get_word_definition(word: str, lang: str, dl_timeout: int, n_retries: int) -> str:
    """Return the definition of a word that's passed to it. Empty if no defs."""
    response = []
    word_text = ""
    try:
        response = requests.get(get_link(lang, word), timeout=dl_timeout).json()
    except requests.exceptions.ConnectionError:
        if n_retries:
            word_text = get_word_definition(word, lang, dl_timeout, n_retries - 1)
        return word_text

    try:
        for word_def in response:
            word_text = ""
            definition = ""

            phonetics = word_def["phonetics"]
            meanings = word_def["meanings"]

            phonetics = [phoenetic["text"] for phoenetic in phonetics]
            word_text = f"<small>{str(phonetics)}</small>"

            for meaning_n, meaning in enumerate(meanings):
                part_of_speech = meaning["partOfSpeech"]
                definition = meaning["definitions"][0]["definition"]
                example = meaning["definitions"][0]["example"]

                word_text += f"<br><b>{meaning_n+1}. </b> <small>{part_of_speech} - </small>{definition} <i> {example} </i>"

            # sometimes there's pronounciation info but not definition
            if definition == "":
                word_text = ""

    except:
        word_text = ""
    return word_text


def add_to_collection(word_defs: dict) -> None:
    """Adds valid words to the collection"""
    for word, definition in word_defs.items():
        note = mw.col.newNote("Basic")
        note["Front"] = word
        note["Back"] = definition
        mw.col.addNote(note)

    mw.col.save()
    return
