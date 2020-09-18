# -*- coding: utf-8 -*-

'''
Text block objects based on PDF raw dict extracted with PyMuPDF.

@created: 2020-07-22
@author: train8808@gmail.com
---
https://pymupdf.readthedocs.io/en/latest/textpage.html

    {
        # raw dict
        # --------------------------------
        'type': 0,
        'bbox': (x0,y0,x1,y1),
        'lines': [ lines ]

        # introduced dict
        # --------------------------------
        'before_space': bs,
        'after_space': as,
        'line_space': ls
    }
'''

from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH

from .Line import Line
from .Lines import Lines
from ..image.ImageSpan import ImageSpan
from ..common.base import RectType, TextDirection, TextAlignment
from ..common.Block import Block
from ..common.utils import RGB_component_from_name
from ..common.constants import DM, DR
from ..common import docx


class TextBlock(Block):
    '''Text block.'''
    def __init__(self, raw:dict={}) -> None:
        # bbox is calculated from contained lines
        # so remove key 'bbox' here
        if 'bbox' in raw: raw.pop('bbox') 
        super(TextBlock, self).__init__(raw)

        # collect lines
        self.lines = Lines(None, self).from_dicts(raw.get('lines', []))

        # set type
        self.set_text_block()

    @property
    def text(self) -> str:
        '''Get text content in block, joning each line with `\n`.'''
        lines_text = [line.text for line in self.lines]
        return '\n'.join(lines_text)

    
    @property
    def text_direction(self):
        '''All lines contained in text block must have same text direction. Otherwise, set normal direction'''            
        res = set(line.text_direction for line in self.lines)
        # consider two text direction only:  left-right, bottom-top
        if TextDirection.IGNORE in res:
            return TextDirection.IGNORE
        elif len(res)==1:
            return list(res)[0]
        else:
            return TextDirection.LEFT_RIGHT


    def set_alignment(self, bbox):
        '''Alignment mode based on lines layout and page bbox.'''
        # NOTE: in PyMuPDF CS, horizontal text direction is same with positive x-axis,
        # while vertical text is on the contrarory, so use f = -1 here
        idx0, idx1, f = (0, 2, 1.0) if self.is_horizontal_text else (3, 1, -1.0)
        d_left   = (self.bbox[idx0] - bbox[idx0]) * f # left margin
        d_right  = (bbox[idx1] - self.bbox[idx1]) * f # right margin
        d_center = (d_left-d_right) / 2.0             # center margin

        # check contained lines in first priority
        X0 = [line.bbox[idx0] for line in self.lines]
        X1 = [line.bbox[idx1] for line in self.lines]
        X  = [(x0+x1)/2.0 for (x0, x1) in zip(X0, X1)]
        left_aligned   = abs(max(X0)-min(X0))<=DM
        right_aligned  = abs(max(X1)-min(X1))<=DM
        center_aligned = abs(max(X)-min(X))<=DM

        if left_aligned and not right_aligned:
            self.alignment = TextAlignment.LEFT
            self.left_space = d_left

        elif right_aligned and not left_aligned: 
            self.alignment = TextAlignment.RIGHT
            self.right_space = d_right

        elif center_aligned and not left_aligned and not right_aligned:
            self.alignment = TextAlignment.CENTER

        else:
            # check position to page bbox further
            if abs(d_center)<DM:
                self.alignment = TextAlignment.CENTER

            elif abs(d_left) <= abs(d_right):
                self.alignment = TextAlignment.LEFT
                self.left_space = d_left

            else:
                self.alignment = TextAlignment.RIGHT
                self.right_space = d_right            
    

    def store(self) -> dict:
        res = super().store()
        res.update({
            'lines': self.lines.store()
        })
        return res


    def add(self, line:Line):
        '''Add line to TextBlock.'''
        self.lines.append(line)


    def join(self):
        '''Merge contained lines horizontally.'''
        self.lines.merge()


    def split(self):
        ''' Split contained lines vertically and create associated text blocks.'''
        blocks = [] # type: list[TextBlock]
        for lines in self.lines.split():
            text_block = TextBlock()
            text_block.lines.reset(list(lines))
            blocks.append(text_block)
        
        return blocks


    def plot(self, page):
        '''Plot block/line/span area, in PDF page.
           ---
            Args: 
              - page: fitz.Page object
        '''
        # block border in blue
        blue = RGB_component_from_name('blue')   
        page.drawRect(self.bbox, color=blue, fill=None, overlay=False)

        # lines and spans
        for line in self.lines:
            # line border in red
            red = RGB_component_from_name('red')
            line.plot(page, red)

            # span regions in random color
            for span in line.spans:
                c = RGB_component_from_name('')                
                span.plot(page, c)


    def contains_discrete_lines(self, distance:float=25, threshold:int=2) -> bool:
        ''' Check whether lines in block are discrete: 
              - the count of lines with a distance larger than `distance` is greater then `threshold`.
              - ImageSpan exists
              - vertical text exists
        '''
        num = len(self.lines)
        if num==1: return False

        # check image spans
        if self.lines.image_spans: return True

        # check text direction
        if self.is_vertical_text: return True

        # check the count of discrete lines
        cnt = 1
        for i in range(num-1):
            line = self.lines[i]
            next_line = self.lines[i+1]

            if line.horizontally_align_with(next_line):
                # horizontally aligned but not in a same row -> discrete block
                if not line.in_same_row(next_line): return True
                
                # otherwise, check the distance only
                elif abs(line.bbox.x1-next_line.bbox.x0) > distance:
                    cnt += 1

        return cnt >= threshold

    
    def parse_text_format(self, rects) -> bool:
        '''parse text format with style represented by rectangles.
            ---
            Args:
              - rects: Shapes, potential styles applied on blocks
        '''
        flag = False

        # use each rectangle (a specific text format) to split line spans
        for rect in rects:

            # a same style rect applies on only one block
            if rect.type != RectType.UNDEFINED: continue

            # any intersection with current block?
            if not self.bbox.intersects(rect.bbox): continue

            # yes, then go further to lines in block            
            for line in self.lines:
                # any intersection in this line?
                intsec = rect.bbox & ( line.bbox + DR )
                
                if not intsec: 
                    if rect.bbox.y1 < line.bbox.y0: break # lines must be sorted in advance
                    continue

                # yes, then try to split the spans in this line
                split_spans = []
                for span in line.spans: 
                    # include image span directly
                    if isinstance(span, ImageSpan): split_spans.append(span)                   

                    # split text span with the format rectangle: span-intersection-span
                    else:
                        spans = span.split(rect, line.is_horizontal_text)
                        split_spans.extend(spans)
                        flag = True
                                                
                # update line spans                
                line.spans.reset(split_spans)

        return flag


    def parse_line_spacing(self):
        '''Calculate average line spacing.

            The layout of pdf text block: line-space-line-space-line, excepting space before first line, 
            i.e. space-line-space-line, when creating paragraph in docx. So, an average line height = space+line.

            Then, the height of first line can be adjusted by updating paragraph before-spacing.
        '''

        # check text direction
        idx = 1 if self.is_horizontal_text else 0

        ref_line = None
        count = 0

        for line in self.lines:
            # count of lines
            if not line.in_same_row(ref_line): count += 1

            # update reference line
            ref_line = line            
        
        bbox = self.lines[0].bbox   # first line
        first_line_height = bbox[idx+2] - bbox[idx]
        block_height = self.bbox[idx+2]-self.bbox[idx]
        
        # average line spacing
        if count > 1:
            line_space = (block_height-first_line_height)/(count-1)
        else:
            line_space = block_height        
        self.line_space = line_space

        # since the line height setting in docx may affect the original bbox in pdf, 
        # it's necessary to update the before spacing:
        # taking bottom left corner of first line as the reference point                
        self.before_space += first_line_height - line_space

        # if before spacing is negative, set to zero and adjust calculated line spacing accordingly
        if self.before_space < 0:
            self.line_space += self.before_space / count
            self.before_space = 0.0


    def make_docx(self, p):
        ''' Create paragraph for a text block.
            ---
            Args:
              - p: docx paragraph instance

            NOTE:
            - the left position of paragraph set by paragraph indent, rather than TAB stop
            - hard line break is used for line in block.

            Generally, a pdf block is a docx paragraph, with block->line as line in paragraph.
            But without the context, it's not able to recognize a block line as word wrap, or a 
            separate line instead. A rough rule used here: block line will be treated as separate 
            line, except this line and next line are indeed in the same line.

            Refer to python-docx doc for details on text format:
            - https://python-docx.readthedocs.io/en/latest/user/text.html
            - https://python-docx.readthedocs.io/en/latest/api/enum/WdAlignParagraph.html#wdparagraphalignment
        '''
        pf = docx.reset_paragraph_format(p)

        # vertical spacing
        before_spacing = max(round(self.before_space, 1), 0.0)
        after_spacing = max(round(self.after_space, 1), 0.0)

        pf.space_before = Pt(before_spacing)
        pf.space_after = Pt(after_spacing)        

        # line spacing
        pf.line_spacing = Pt(round(self.line_space, 1))

        # horizontal alignment
        if self.alignment==TextAlignment.LEFT:
            pf.alignment = WD_ALIGN_PARAGRAPH.LEFT
            pf.left_indent  = Pt(round(self.left_space, 1))

        elif self.alignment==TextAlignment.RIGHT:
            pf.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            pf.right_indent  = Pt(round(self.right_space, 1))

        else:
            pf.alignment = WD_ALIGN_PARAGRAPH.CENTER

        # add line by line
        current_pos = 0.0
        for i, line in enumerate(self.lines):

            # add line
            for span in line.spans: span.make_docx(p)

            # hard line break is necessary, otherwise the paragraph structure may change in docx,
            # which leads to the pdf-based layout calculation becomes wrong
            line_break = True

            # no more lines after last line
            if line==self.lines[-1]: line_break = False            
            
            # do not break line if they're indeed in same line
            elif line.in_same_row(self.lines[i+1]):
                line_break = False
            
            if line_break: p.add_run('\n')

        return p
