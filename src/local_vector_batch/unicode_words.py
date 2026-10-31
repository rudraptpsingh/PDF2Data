"""Python-word-compatible tokens that retain every Unicode combining mark.

Only word-token membership changes. Punctuation, numeric and operator parsers
remain separate. Native re.Match objects preserve exact codepoint offsets.
"""
from functools import lru_cache
import re,unicodedata


@lru_cache(maxsize=128)
def _pattern(marks):
    return re.compile(r'[\w'+re.escape(marks)+r']+',re.UNICODE)if marks else re.compile(r'\w+',re.UNICODE)


class MarkPreservingWords:
    @staticmethod
    def _for(text):
        marks=''if text.isascii()else''.join(sorted({c for c in text if unicodedata.category(c).startswith('M')}))
        return _pattern(marks)

    def findall(self,text,pos=0,endpos=None):
        pattern=self._for(text)
        return pattern.findall(text,pos,len(text)if endpos is None else endpos)

    def finditer(self,text,pos=0,endpos=None):
        pattern=self._for(text)
        return pattern.finditer(text,pos,len(text)if endpos is None else endpos)


WORD=MarkPreservingWords()
