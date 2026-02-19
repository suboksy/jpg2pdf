# jpg2pdf

Convert JPG Files In A Directory To Simple PDF

Python program takes all jpg and png files in a directory and outputs a PDF file.  If README.md found in the directory, the markdown will be rendered as the first page, allowing for prefacing notes or comments.

**Dependencies** (all standard, likely already installed):
```
pip install reportlab Pillow markdown beautifulsoup4 pypdf
```

```
python jpg2pdf.py ./my_photos
```

## Notes
- Collects all `.jpg`/`.jpeg`/`.png` files in the given subdirectory, sorted alphanumerically.
- If `README.md` is present, it's rendered first using `markdown` + BeautifulSoup + reportlab platypus — supporting headings, bold/italic, inline code, fenced code blocks, bullet lists, blockquotes, and horizontal rules.
- Each image gets its own page, drawn with reportlab's canvas (not platypus flowables) so positioning is exact: images are width-fitted to the 7" content area (8.5" − 0.75"×2 margins) and clamped to the 9.5" content height (11" − 0.75"×2). Tall portrait images shrink to fit height, wide landscape images fill the width. Images are horizontally centered.
- All pages are merged into a single PDF via `pypdf`, written as `<subdirectory_name>.pdf` in the parent of the input directory.

## License

This utility is provided as-is for educational and research purposes (MIT License).
