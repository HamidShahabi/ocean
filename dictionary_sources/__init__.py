"""Dictionary sources: one module per dataset, one registry the lookup engine merges.

Every source reads the local database (lexicon_db.LexiconDB, built by tools/build_lexicon_db.py), so lookups need
no network. To fix, replace or switch off a source, touch only its module (or REGISTRY.set_enabled(name, False)).
To add one, subclass DictionarySource and register it below; position in the list is merge order.
"""
from .base import DictionarySource, LookupContext, PartialEntry, SourceRegistry, merge_entries
from .corpora import CorporaSource
from .kaikki import KaikkiSource
from .urban_dictionary import UrbanDictionarySource

REGISTRY = SourceRegistry()
# Wiktionary first (its IPA, senses, synonyms and part of speech win), slang after it (only where Wiktionary has
# nothing or the slang is the main use), sentence pairs last.
for _source in (
    KaikkiSource(),
    UrbanDictionarySource(),
    CorporaSource(),
):
    REGISTRY.register(_source)
del _source

__all__ = [
    "REGISTRY", "SourceRegistry", "DictionarySource", "LookupContext", "PartialEntry", "merge_entries",
]
