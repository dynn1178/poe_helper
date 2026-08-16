"""Item price checking against the official trade site.

Three pieces, deliberately separated so each can be tested without the other
two: :mod:`api` talks to the trade site, :mod:`item` turns the text the game
puts on the clipboard into a structured item, and :mod:`query` turns that
item into a search the site understands.
"""
