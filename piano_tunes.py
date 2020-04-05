import sublime, sublime_plugin
from dataclasses import dataclass
from typing import Iterable, NamedTuple
#import itertools
from abc import ABC

class Token(NamedTuple):
    region: sublime.Region
    scope: str
    text: str

class NoteInfo(NamedTuple):
    octave: int
    note_index: int
    length: int

@dataclass
class TuneInstruction(ABC):
    span: sublime.Region
    value: int

@dataclass
class NoteInstruction(TuneInstruction):
    pass

@dataclass
class MultipleNotesDelimiterInstruction(TuneInstruction):
    pass

@dataclass
class LengthInstruction(TuneInstruction):
    pass

@dataclass
class AbsoluteOctaveInstruction(TuneInstruction):
    pass

@dataclass
class RelativeOctaveInstruction(TuneInstruction):
    pass

@dataclass
class TempoInstruction(TuneInstruction):
    pass

@dataclass
class PauseInstruction(TuneInstruction):
    pass

def get_tokens_from_regions(view, regions):
    for region in regions:
        tokens = view.extract_tokens_with_scopes(region)
        # TODO: maybe better performance-wise to grab all the text from the region at once, rather than requesting it for each individual token separately
        #text = view.substr(region)

        for token in tokens:
            yield Token(region=token[0], scope=token[1], text=view.substr(token[0]))

def parse_piano_tune(tokens: Iterable[Token]):
    notes_solfege = 'do do# re re# mi fa fa# sol sol# la la# si'.split() # TODO: reuse this from PianoMidi
    notes_letters = 'c c# d d# e f f# g g# a a# b'.split()

    it = iter(tokens)
    take_next = True
    while True:
        if take_next:
            try:
                current_token = next(it)
            except StopIteration:
                break
        else:
            take_next = True

        start_region = current_token.region

        if sublime.score_selector(current_token.scope, 'keyword.operator.bitwise.octave'):
            if current_token.text == '<':
                yield RelativeOctaveInstruction(current_token.region, -1)
            elif current_token.text == '>':
                yield RelativeOctaveInstruction(current_token.region, 1)
        elif sublime.score_selector(current_token.scope, 'keyword.operator.octave'):
            current_token = next(it)
            yield AbsoluteOctaveInstruction(current_token.region.cover(start_region), int(current_token.text))
        elif sublime.score_selector(current_token.scope, 'keyword.operator.tempo'):
            current_token = next(it)
            yield TempoInstruction(current_token.region.cover(start_region), int(current_token.text))
        elif sublime.score_selector(current_token.scope, 'keyword.operator.length'):
            current_token = next(it)
            yield LengthInstruction(current_token.region.cover(start_region), int(current_token.text))
        elif sublime.score_selector(current_token.scope, 'keyword.operator.pause'):
            current_token = next(it)
            yield LengthInstruction(current_token.region.cover(start_region), int(current_token.text))
        elif sublime.score_selector(current_token.scope, 'constant.language.note'):
            if current_token.region.size() == 1:
                # note_index = (ord(current_token.text) - ord('c')) * 2
                # if note_index < 0:
                #     note_index += 12
                note_index = notes_letters.index(current_token.text)
            else:
                note_index = notes_solfege.index(current_token.text)

            prev_token = current_token
            current_token = next(it)
            if sublime.score_selector(current_token.scope, 'constant.language.sharp'):
                note_index += 1
                yield NoteInstruction(current_token.region.cover(start_region), note_index)
            else:
                take_next = False
                yield NoteInstruction(prev_token.region, note_index)
        elif sublime.score_selector(current_token.scope, 'keyword.operator.simultaneous'):
            yield MultipleNotesDelimiterInstruction(current_token.region, 0)
        # TODO: labels and label references
