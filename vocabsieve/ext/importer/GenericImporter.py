from PySide6.QtWidgets import *
from PySide6.QtCore import *
from PySide6.QtGui import *
from io import BytesIO
import os
import re
import glob
import json
from pathlib import Path
from difflib import SequenceMatcher
from sentence_splitter import split_text_into_sentences
from vocabsieve.tools import addNotes
from vocabsieve.dictionary import getAudio
from datetime import datetime
from itertools import compress
from slpp import slpp
from lxml import etree
from ebooklib import epub, ITEM_DOCUMENT

from .utils import *

class GenericImporter(QDialog):
    """
    This class implements the UI for extracting highlights.
    Subclass it and override getNotes to have a new importer
    """
    def __init__(self, parent, src_name="Generic"):
        super().__init__(parent)
        self.settings = parent.settings
        self.lang = parent.settings.value('target_language')
        self.setWindowTitle(f"Import {src_name} highlights")
        self.parent = parent
        self.selected_highlight_items = []
        self.resize(700, 500)
        self.src_name = src_name
        self.orig_lookup_terms, self.orig_sentences, self.orig_dates, self.orig_book_names = self.getNotes()
        self.orig_dates_day = [date[:10] for date in self.orig_dates]
        self.datewidget = QComboBox()
        self.datewidget.addItems(sorted(set(self.orig_dates_day)))
        self.datewidget.currentTextChanged.connect(self.updateHighlightCount)

        self.layout = QFormLayout(self)
        self.lookup_button = QPushButton("Look up currently selected")
        self.lookup_button.clicked.connect(self.defineWords)
        self.layout.addRow(QLabel(
            f"<h2>Import {src_name} highlights</h2>"
        ))
        # Source selector, for selecting which books to include
        self.src_selector = QWidget()
        self.src_checkboxes = []
        self.src_selector.layout = QVBoxLayout(self.src_selector)
        self.src_selector.layout.addWidget(QLabel("<h3>Select books to extract highlights from</h3>"))
    
        for book_name in set(self.orig_book_names):
            self.src_checkboxes.append(
                QCheckBox(truncate_middle(book_name, 90)))
            self.src_selector.layout.addWidget(self.src_checkboxes[-1])
            self.src_checkboxes[-1].clicked.connect(self.updateHighlightCount)
        
        self.layout.addRow(self.src_selector)
        self.layout.addRow("Use notes starting from: ", self.datewidget)
        self.notes_count_label = QLabel()
        self.layout.addRow(self.notes_count_label, self.lookup_button)

        self.definition_count_label = QLabel()
        self.anki_button = QPushButton("Add notes to Anki")
        self.anki_button.setEnabled(False)
        self.anki_button.clicked.connect(self.to_anki)
        self.layout.addRow(self.definition_count_label, self.anki_button)

    def getNotes(self):
        """
        Returns a tuple of four tuples of equal length
        Respectively, lookup terms (highlights), sentences, dates, and book names
        All the file parsing should happen here.
        """
        return ((), (), (), ())

    def updateHighlightCount(self):
        start_date = self.datewidget.currentText()
        selected_book_names = []
        for checkbox in self.src_checkboxes:
            if checkbox.isChecked():
                selected_book_names.append(checkbox.text())
        self.selected_highlight_items = self.filterHighlights(start_date, selected_book_names)
        
        self.notes_count_label.setText(f"{len(self.selected_highlight_items)} highlights selected")

    def filterHighlights(self, start_date, book_names):
        try:
            lookup_terms, sentences, book_names = zip(*compress(
                zip(self.orig_lookup_terms, self.orig_sentences, self.orig_book_names), 
                map(lambda b, d: d[:10] >= start_date and b in book_names, self.orig_book_names, self.orig_dates)
                ))
        except ValueError:
            lookup_terms, sentences, book_names = [],[],[]
        return list(zip(lookup_terms, sentences, book_names))


    def defineWords(self):
        self.sentences = []
        self.words = []
        self.definitions = []
        self.definition2s = []
        self.audio_paths = []
        self.book_names = []
    

        count = 0
        for lookup_term, sentence, book_name in self.selected_highlight_items:
            # Remove punctuations
            word = re.sub('[\\?\\.!«»…,()\\[\\]]*', "", lookup_term)

            if sentence:
                if self.settings.value("bold_word", True, type=bool):
                    self.sentences.append(sentence.replace("_", "").replace(word, f"__{word}__"))
                    
                else:
                    self.sentences.append(sentence)
                item = self.parent.lookup(word, record=False)
                if not item['definition'].startswith("<b>Definition for"):
                    count += 1
                    self.words.append(item['word'])
                    self.definitions.append(item['definition'])
                    self.definition_count_label.setText(
                        str(count) + " definitions found")
                    QApplication.processEvents()
                else:
                    self.words.append(word)
                    self.definitions.append("")
                self.definition2s.append(item.get('definition2', ""))

                audio_path = ""
                if self.settings.value("audio_dict", "Forvo (all)") != "<disabled>":
                    try:
                        audios = getAudio(
                                word,
                                self.settings.value("target_language", 'en'),
                                dictionary=self.settings.value("audio_dict", "Forvo (all)"),
                                custom_dicts=json.loads(
                                    self.settings.value("custom_dicts", '[]')))
                    except Exception:
                        audios = {}
                    if audios:
                        # First item
                        audio_path = audios[next(iter(audios))]
                self.audio_paths.append(audio_path)
                self.book_names.append(book_name)
            else:
                print("no sentence")
                #self.sentences.append("")
                #self.definitions.append("")
                #self.words.append("")
                #self.definition2s.append("")
                #self.audio_paths.append("")

        self.anki_button.setEnabled(True)
        print("Lengths", len(self.sentences), len(self.words), len(self.definitions), len(self.audio_paths))
    def to_anki(self):
        notes = []
        for word, sentence, definition, definition2, audio_path, book_name in zip(
                self.words, self.sentences, self.definitions, self.definition2s, self.audio_paths, self.book_names):
            if word and sentence and definition:
                if self.settings.value("bold_word", True, type=bool):
                    sentence = re.sub(
                        r"__([ \w]+)__",
                        r"<strong>\1</strong>",
                        sentence
                        )
                tags = " ".join([
                    self.parent.settings.value("tags", "vocabsieve").strip(),
                    self.src_name.lower(),
                    book_name.replace(" ","_")
                    ]
                    )
                content = {
                    "deckName": self.parent.settings.value("deck_name"),
                    "modelName": self.parent.settings.value("note_type"),
                    "fields": {
                        self.parent.settings.value("sentence_field"): sentence,
                        self.parent.settings.value("word_field"): word,
                    },
                    "tags": tags.split(" ")
                }
                definition = definition.replace("\n", "<br>")
                content['fields'][self.parent.settings.value(
                    'definition_field')] = definition
                if self.settings.value("dict_source2", "<disabled>") != '<disabled>':
                    definition2 = definition2.replace("\n", "<br>")
                    content['fields'][self.parent.settings.value('definition2_field')] = definition2
                if self.settings.value("audio_dict", "<disabled>") != '<disabled>' and audio_path:
                    content['audio'] = {}
                    if audio_path.startswith("https://") or audio_path.startswith("http://"):
                        content['audio']['url'] = audio_path
                    else:
                        content['audio']['path'] = audio_path
                    content['audio']['filename'] = audio_path.replace("\\", "/").split("/")[-1]
                    content['audio']['fields'] = [self.settings.value('pronunciation_field')]

                print(content)
                notes.append(content)
        res = addNotes(self.parent.settings.value("anki_api"), notes)
        self.layout.addRow(QLabel(
            QDateTime.currentDateTime().toString('[hh:mm:ss]') + " "
            + str(len(notes)) 
            + " notes have been exported, of which " 
            + str(len([i for i in res if i]))
            + " were successfully added to your collection."))