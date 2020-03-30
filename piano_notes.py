from dataclasses import dataclass
from typing import Iterable, NamedTuple

@dataclass
class PianoNote:
    notes_solfege = 'do do# re re# mi fa fa# sol sol# la la# si'.split()
    notes_letters = 'c c# d d# e f f# g g# a a# b'.split()

    octave: int
    note_index: int
    length: int # TODO: validate that lengths (here and for pause) are >= 1 and <= 64?

@dataclass
class PianoNotes:
    notes: Iterable[PianoNote]

@dataclass
class Pause:
    length: int

@dataclass
class Tempo:
    bpm: int # quarter notes per minute

def get_midi_notes(song, octave=4):
    tokens = song.split()
    length = 4
    simultaneous_notes = None
    
    # TODO: a way to specify labels and reference them
    #       i.e. Chorus:
    #            ...
    #            &Chorus
    
    for token in tokens:
        if token == '>':
            octave = octave + 1
        elif token == '<':
            octave = octave - 1
        elif token == '/':
            if simultaneous_notes:
                yield PianoNotes(simultaneous_notes)
                simultaneous_notes = None
            else:
                simultaneous_notes = list()
        else:
            try:
                token_without_hash = token.strip('#')
                note_index = (PianoNote.notes_letters if len(token_without_hash) == 1 else PianoNote.notes_solfege).index(token)
                note = PianoNote(octave=octave, note_index=note_index, length=length)
                
                if simultaneous_notes is not None:
                    simultaneous_notes.append(note)
                else:
                    yield note
            except ValueError:
                try:
                    value = int(token[1:])
                except ValueError as ex:
                    print('invalid token "', token, '": ', ex)
                    return
                
                if token.startswith('t'):
                    yield Tempo(value)
                elif token.startswith('l'):
                    length = value
                elif token.startswith('p'):
                    yield Pause(value or length)
                elif token.startswith('o'):
                    octave = value
    
    # if the / was unclosed at the end of the song
    if simultaneous_notes:
        yield PianoNotes(simultaneous_notes)
