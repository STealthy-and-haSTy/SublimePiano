import sublime
from dataclasses import dataclass
from typing import Iterable, NamedTuple, Union
import mido
from abc import ABC
from operator import itemgetter, attrgetter
from itertools import chain


class Token(NamedTuple):
    region: sublime.Region
    scope: str
    text: str

@dataclass
class TuneInstruction(ABC):
    span: sublime.Region
    value: Union[int, str]

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

@dataclass
class LabelStartInstruction(TuneInstruction):
    pass

@dataclass
class LabelEndInstruction(TuneInstruction):
    pass

@dataclass
class LabelReferenceInstruction(TuneInstruction):
    pass

class TuneState(NamedTuple):
    tempo: int
    current_octave: int
    current_length: int
    time_elapsed: float
    simultaneous_notes: bool = False
    instruction: TuneInstruction = None
    duration: float = 0

@dataclass
class PianoTuneMidiHighlight:
    state: TuneState
    on: bool
    time_elapsed: float

    def to_midi_message(self, time_delta):
        if not isinstance(self.state.instruction, NoteInstruction):
            return None
        octave = self.state.current_octave
        note_index = self.state.instruction.value
        return mido.Message('note_' + ('on' if self.on else 'off'), note=note_to_midi_note(octave, note_index), time=int(time_delta))


def note_to_midi_note(octave, note_index):
    return octave * 12 + note_index

def get_tokens_from_regions(view, regions):
    """effectively a wrapper around View.extract_tokens_with_scopes
    to also return the text of the token in the 2nd index of the tuple."""
    for region in regions:
        tokens = view.extract_tokens_with_scopes(region)
        # NOTE: rather than just doing a `text=view.substr(token[0])` for each token
        # i.e. requesting the text across the plugin_host for each individual token separately,
        # we grab all the text from the region at once and slice that, for better performance
        region_text = view.substr(region)

        for token in tokens:
            yield Token(region=token[0], scope=token[1], text=region_text[token[0].begin() - region.begin():token[0].end() - region.begin()])

def parse_piano_tune(tokens: Iterable[Token]):
    """convert raw tokens from the syntax definition to piano tune instruction tokens"""
    notes_solfege = 'do do# re re# mi fa fa# sol sol# la la# si'.split() # TODO: reuse this from PianoMidi
    notes_letters = 'c c# d d# e f f# g g# a a# b'.split()

    it = iter(tokens)
    take_next = True
    while True:
        if take_next:
            current_token = next(it, None)
        else:
            take_next = True
        if current_token is None:
            break

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
            yield PauseInstruction(current_token.region.cover(start_region), int(current_token.text))
        elif sublime.score_selector(current_token.scope, 'constant.language.note'):
            find_note_in = notes_letters if current_token.region.size() == 1 else notes_solfege
            note_index = find_note_in.index(current_token.text.lower())
            
            prev_token = current_token
            current_token = next(it, None)
            if current_token and sublime.score_selector(current_token.scope, 'constant.language.sharp'):
                note_index += 1
                yield NoteInstruction(current_token.region.cover(start_region), note_index)
            else:
                take_next = False
                yield NoteInstruction(prev_token.region, note_index)
        elif sublime.score_selector(current_token.scope, 'keyword.operator.simultaneous'):
            yield MultipleNotesDelimiterInstruction(current_token.region, 0)
        elif sublime.score_selector(current_token.scope, 'entity.name.label'):
            label_text = current_token.text
            current_token = next(it)
            yield LabelStartInstruction(current_token.region.cover(start_region), label_text)
        elif sublime.score_selector(current_token.scope, 'punctuation.section.block.end'):
            yield LabelEndInstruction(current_token.region, None) # TODO: store a stack of labels to refer to which label it ends here?
        elif sublime.score_selector(current_token.scope, 'keyword.control.flow'):
            current_token = next(it)
            yield LabelReferenceInstruction(current_token.region.cover(start_region), current_token.text)

def calculate_duration(tempo: int, note_length: int):
        return (60 / tempo) / note_length * 4 * 1000

def resolve_piano_tune_instructions(instructions: Iterable[NoteInstruction], default_state=TuneState(120, 4, 8, 0, False, None, 0)):
    """from the piano tune instructions, determine the state at each instruction,
    specifically how much time has passed since the beginning of the tune"""
    state = default_state
    time_elapsed = 0
    max_time_elapsed = 0
    labels = dict()
    states = list()
    active_label_definition = None

    token = next(instructions, None)
    while token is not None:
        time_elapsed = state.time_elapsed
        if not state.simultaneous_notes or not isinstance(state.instruction, NoteInstruction):
            time_elapsed += state.duration
            max_time_elapsed = max(time_elapsed, max_time_elapsed)
        else:
            max_time_elapsed = max(time_elapsed + state.duration, max_time_elapsed)
        state = state._replace(instruction=token, time_elapsed=time_elapsed, duration=0)

        if isinstance(token, LabelStartInstruction):
            if active_label_definition:
                labels[active_label_definition]['instructions'].append(token)
            active_label_definition = token.value
            labels[active_label_definition] = { 'begin': token.span, 'end': None, 'instructions': list() }
        elif isinstance(token, LabelEndInstruction):
            labels[active_label_definition]['end'] = token.span
            label_instructions = labels[active_label_definition]['instructions']
            active_label_definition = next((key for key in reversed(labels.keys()) if labels[key]['end'] is None), None)
            # correctly handle nested instructions
            if active_label_definition:
                labels[active_label_definition]['instructions'] += label_instructions
                labels[active_label_definition]['instructions'].append(token)
        elif isinstance(token, LabelReferenceInstruction):
            if active_label_definition:
                # prevent recursive label instructions
                if active_label_definition == token.value:
                    # TODO: log a warning?
                    continue
                labels[active_label_definition]['instructions'].append(token)
            # TODO: support label references to labels that haven't yet been parsed?
            label = labels[token.value]
            resolved = resolve_piano_tune_instructions(iter(label['instructions']), state)
            # ensure the state duration covers all label instructions, so the reference can be highlighted...
            if resolved:
                state = state._replace(duration=resolved[-1].time_elapsed + resolved[-1].duration - time_elapsed)
            states.append(state)
            if resolved:
                states += resolved
                state = resolved[-1]
        else:
            if active_label_definition:
                labels[active_label_definition]['instructions'].append(token)
            if isinstance(token, RelativeOctaveInstruction):
                state = state._replace(current_octave=state.current_octave + token.value)
            elif isinstance(token, AbsoluteOctaveInstruction):
                state = state._replace(current_octave=token.value)
            elif isinstance(token, TempoInstruction):
                state = state._replace(tempo=token.value)
            elif isinstance(token, LengthInstruction):
                state = state._replace(current_length=token.value)
            elif isinstance(token, PauseInstruction):
                state = state._replace(duration=calculate_duration(state.tempo, token.value or state.current_length))
            elif isinstance(token, NoteInstruction):
                state = state._replace(duration=calculate_duration(state.tempo, state.current_length))
            elif isinstance(token, MultipleNotesDelimiterInstruction):
                state = state._replace(simultaneous_notes=not state.simultaneous_notes, time_elapsed=max_time_elapsed)
            states.append(state)
        token = next(instructions, None)
    return states

def convert_piano_tune_to_midi(tune_states):
    """from the piano tune states, return the timings for what tokens to highlight
    and what midi notes to play"""

    # reduce states to those that are notes or something to highlight, like pauses
    def state_is_interesting(state):
        return isinstance(state.instruction, NoteInstruction) \
            or isinstance(state.instruction, PauseInstruction) \
            or isinstance(state.instruction, LabelReferenceInstruction)
    tune_states = list(state for state in tune_states if state_is_interesting(state))

    # for each state, add an "off" state after the duration
    add_states = list()
    for state in tune_states:
        add_states.append(
            PianoTuneMidiHighlight(state, True, state.time_elapsed)
        )
        add_states.append(
            PianoTuneMidiHighlight(state, False, state.time_elapsed + state.duration)
        )
    add_states.sort(key=attrgetter('time_elapsed'))
    return add_states
