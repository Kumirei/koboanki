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
from string import punctuation


### IO


def get_config() -> dict:
    """Opens and returns the config. TODO: link with the verifcation function."""
    config = mw.addonManager.getConfig(__name__)
    return config


def get_blacklist() -> list:
    """Opens and normalises the blacklist. TODO: create blacklist if there isn't one."""
    user_files_dir = path.join(mw.pm.addonFolder(), "koboanki", "user_files")
    with open(path.join(user_files_dir, "blacklist.json")) as file:
        blacklist = json.load(file)
    normal_blacklist = [normalise_word(word) for word in blacklist]
    return normal_blacklist


def try_link(link) -> bool:
    """Verifies if a link is valid. Unvalid links don't connect or 404."""
    valid = True
    try:
        response = requests.get(link)
        if response.status_code == 404:
            valid = False
    except requests.exceptions.ConnectionError:
        valid = False

    return valid


def get_file_location() -> str:
    """Returns the kobo db file location. Empty if error or not found."""
    folder_name = QFileDialog.getExistingDirectory(
        None, "Select KOBO drive", path.expanduser("~"), QFileDialog.ShowDirsOnly
    )

    if folder_name:
        file_location = path.join(folder_name, ".kobo", "KoboReader.sqlite")
        if not (path.exists(file_location) and path.isfile(file_location)):
            showInfo(
                f"File path not found: {file_location}"
            )  # TODO: remove and use verificaction function
            file_location = ""
    else:
        file_location = ""

    return file_location


def get_kobo_wordlist(file_location: str) -> list:
    """Opens the kobo file and returns a list of saved words (normalised)."""
    connection = sqlite3.connect(file_location)
    cursor = connection.cursor()
    wordlist = [
        row[0] for row in cursor.execute("SELECT text from WordList").fetchall()
    ]
    normal_wordlist = [normalise_word(word) for word in wordlist]
    return normal_wordlist


def get_deck_dict() -> dict:
    """Gets the list of anki decks with some metadata."""
    deck_list = mw.col.decks.all_names_and_ids()
    deck_dict = {}
    for deck in deck_list:
        split_deck = str(deck).split("\n")
        id = split_deck[0].split(" ")[1]
        name = split_deck[1].split('"')[1]
        deck_dict[name] = id
    return deck_dict


def add_to_collection(word_defs, deck_id: int) -> None:
    """Adds valid words to the collection"""
    for word in word_defs:
        model = mw.col.models.by_name("Kobo")
        note = mw.col.new_note(model)  # type: ignore
        note["Word"] = word["word"]
        note["Expression"] = word["expression"]
        note["Reading"] = word["reading"]
        note["Definition"] = word["definition"]
        note["Pos"] = word["pos"]
        note.tags.append("koboanki")
        # mw.col.addNote(note)
        mw.col.add_note(note, deck_id)  # type: ignore

    mw.col.save()
    return


### Verification
# TODO

# See https://github.com/meetDeveloper/freeDictionaryAPI/blob/master/modules/utils.js
SUPPORTED_LANGUAGES = (
        'hi', 	 # Hindi
        'en',    # English (US)
        'en-uk', # English (UK)
        'es', 	 # Spanish
        'fr',	 # French
        'ja',    # Japanese
        'cs',    # Czech
        'nl',    # Dutch
        'sk',    # Slovak
        'ru',	 # Russian
        'de', 	 # German
        'it', 	 # Italian
        'ko',	 # Korean
        'pt-BR', # Brazilian Portuguese
        'ar',    # Arabic
        'tr'     # Turkish
)

def verify_config(config: dict) -> bool:
    if not config:
        showInfo("Config file is empty")
        return False
    if not "language_list" in config:
        showInfo("Config file does not contain a language list")
        return False
    if len(config["language_list"]) == 0:
        showInfo("Language list is empty")
        return False

    failed_codes = [code for code in config["language_list"] if code not in SUPPORTED_LANGUAGES]
    if failed_codes:
        showInfo(f"The following language codes are not valid: {failed_codes}")
        return False
    return True


### Interfaces


def get_words(config):
    """Calls all functions. Gets words."""
    if not verify_config(config):
        return

    blacklist = get_blacklist()
    if not blacklist:
        showInfo("No valid blacklist found")
        return

    # get folder name
    file_location = get_file_location()
    if not file_location:
        return

    # read in the word list
    wordlist = get_kobo_wordlist(file_location)
    if not wordlist:
        showInfo("No saved words found")
        return

    # check internet connection
    if not try_link(get_link("en", "test")):
        showInfo("Can't access server, faulty internet connection?")
        return

    # find new words, get definitions, add to collection
    new_wordlist = get_new_wordlist(wordlist)
    not_blacklisted = [word for word in new_wordlist if word not in blacklist]
    word_defs = get_definitions(not_blacklisted, config)

    return word_defs


### Actual utils


def normalise_word(word: str) -> str:
    """Lowers the case of all characters and removes punctuation from the end of words."""
    return (word[:-1] + word[-1].strip(punctuation)).lower()


def get_link(language_code: str, word: str) -> str:
    """Creates a dictionary link from a language code and word."""
    return f"https://api.dictionaryapi.dev/api/v2/entries/{language_code}/{word}"


def get_new_wordlist(kobo_wordlist: list) -> list:
    """Returns a list of only words not already added to anki."""
    print("kobo_wordlist", kobo_wordlist)
    ids = mw.col.find_notes("")
    anki_wordlist = [mw.col.getNote(id_).items()[0][1] for id_ in ids]
    new_wordlist = [word for word in kobo_wordlist if word not in anki_wordlist]
    # new_wordlist = ["hi", "hello", "bye", "test", "double", "triple"]
    # new_wordlist = new_wordlist[:3]  # TEMP
    print("new_wordlist", new_wordlist)
    return new_wordlist


def get_definitions(wordlist: list, config: dict) -> dict:
    """Concurrently find defintions for all words"""
    queue = Queue(maxsize=0)
    num_theads = min(config["dl_threads"], len(wordlist))
    definitions = []
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

    return definitions


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
            break
        if not definition:
            queue.task_done()
            continue

        definitions.append(definition)
        print(definition)
        queue.task_done()
    return True


def get_word_definition(word: str, lang: str, dl_timeout: int, n_retries: int) -> str:
    """Return the definition of a word that's passed to it. Empty if no defs."""
    response = []
    word_text = ""
    reading = ""
    pos = ""
    definition = ""
    example = ""
    expression = ""
    try:
        if lang == "ja":
            response = requests.get(f"https://jisho.org/api/v1/search/words?keyword={word}", timeout=dl_timeout).json()
            # print(response["data"])
        else:
            response = requests.get(get_link(lang, word), timeout=dl_timeout).json()
    except:  # TODO: test this
        return False

    try:
        if lang == "ja":
            reading = "、".join({val: None for val in [jp["reading"] for jp in response["data"][0]["japanese"]]}.keys())
            definition = ", ".join(response["data"][0]["senses"][0]["english_definitions"])
            pos = ", ".join(response["data"][0]["senses"][0]["parts_of_speech"])
            expression = response["data"][0]["slug"]
        else:
            for word_def in response:
                word_text = ""
                definition = ""

                phonetics = word_def["phonetics"]
                definition = word_def["meanings"]
                example = definition[0]["definitions"][0]["example"]

                phonetics = [phoenetic["text"] for phoenetic in phonetics]
                word_text = f"<small>{str(phonetics)}</small>"

                # for meaning_n, meaning in enumerate(meanings):
                #     pos = meaning["partOfSpeech"]
                #     definition = meaning["definitions"][0]["definition"]
                #     example = meaning["definitions"][0]["example"]

                    # word_text += f"<br><b>{meaning_n+1}. </b> <small>{part_of_speech} - </small>{definition} <i> {example} </i>"

                # sometimes there's pronounciation info but not definition
                if definition == "":
                    word_text = ""

    except:
        word_text = ""
    return {"word": word, "reading": reading, "definition": definition, "pos": pos, "example": example, "expression": expression}
