"""Dictionary sources: one self-contained module per site, one registry the lookup engine merges.

To fix, replace or switch off a site, touch only its module (or REGISTRY.set_enabled(name, False)).
To add one, subclass DictionarySource and register it below; position in the list is merge order.

Seams for later work: REGISTRY.set_enabled / disabled_names (Settings), LookupContext.offline with
DictionarySource.needs_network (offline-only mode), LookupContext.requester / .request (replace the network in
tests). The per-lookup request cache is not here: it sits in lexical_engine.smart_request, so it covers every source.
"""
from .base import DictionarySource, LookupContext, PartialEntry, SourceRegistry, merge_entries
from .cambridge import CambridgeSource
from .datamuse import DatamuseFrequencySource, DatamuseSource
from .dictionaryapi import DictionaryApiSource
from .longman import LongmanSource
from .offline import CuratedSynonymsSource, IdiomFallbackSource, OfflineIdiomsSource
from .tatoeba import TatoebaSource
from .thefreedictionary import TheFreeDictionarySource
from .wiktionary import WiktionarySource
from .wordnet import WordNetSource

REGISTRY = SourceRegistry()
# The order is what the old engine produced: phrase sources first, then Cambridge and Longman, then
# the dictionaryapi.dev/WordNet group, with Datamuse after the sources whose part of speech it needs.
for _source in (
    OfflineIdiomsSource(),
    TheFreeDictionarySource(),
    WiktionarySource(),
    IdiomFallbackSource(),
    CambridgeSource(),
    LongmanSource(),
    CuratedSynonymsSource(),
    DictionaryApiSource(),
    WordNetSource(),
    DatamuseSource(),
    DatamuseFrequencySource(),
    TatoebaSource(),
):
    REGISTRY.register(_source)
del _source

__all__ = [
    "REGISTRY", "SourceRegistry", "DictionarySource", "LookupContext", "PartialEntry", "merge_entries",
]
