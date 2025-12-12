// Maximally Minimal Swiss Template
// Fonts: Inter + Space Mono
// Philosophy: Content first, rigid grid, minimal decoration, clear hierarchy.

#let project(
  title: "",
  subtitle: none,
  authors: (),
  date: none,
  abstract: none,
  body
) = {
  // 1. Setup
  set document(author: authors, title: title)
  
  // 3. Typography (Global)
  set text(
    font: ("Inter", "Helvetica", "Arial"), 
    size: 10.5pt, 
    weight: "regular",
    lang: "en"
  )

  set par(
    justify: false, 
    leading: 0.6em, 
    spacing: 1.2em  // One skipped line
  )

  // 4. Headings (Numbers in Margin, Titles in Body)
  set heading(numbering: "1.1")
  show heading: it => {
    let size = 1em 
    
    let weight = "black" 
    let fill = if it.level == 1 { black } else { luma(100) } 
    
    // Max spacing = one skipped 10.5pt line (~1.5em)
    let above = if it.level == 1 { 1.5em } else { 1em }
    let below = 0.5em

    v(above, weak: true)
    block(below: below, sticky: true)[
      #if it.numbering != none {
        // Place number in margin
        place(dx: -5.5cm, box(width: 5cm, align(right, 
           text(font: ("Inter", "Helvetica"), weight: "black", size: size, fill: fill)[#counter(heading).display(it.numbering)]
        )))
      }
      #text(font: ("Inter", "Helvetica"), weight: weight, size: size, fill: fill, it.body)
    ]
  }

  // Footnotes: Remove separator line, Use Space Mono
  set footnote.entry(separator: none)
  show footnote.entry: set text(font: ("Space Mono", "Courier New"), size: 8pt)

  // Bibliography: Ensure Inter font, 8pt, APA style
  set bibliography(style: "apa")
  show bibliography: set text(font: ("Inter", "Helvetica"), size: 8pt)

  // 6. Outline (TOC) Styling
  show outline.entry: it => {
    let el = it.element
    let num = if el.has("numbering") and el.numbering != none {
       numbering(el.numbering, ..counter(heading).at(el.location()))
    } else { none }
    let page_num = counter(page).at(el.location()).first()

    // Line above entry
    line(length: 100%, stroke: 1pt)
    v(0.5em)
    
    // Grid layout
    grid(
      columns: (3em, 1fr, auto), 
      column-gutter: 0.5em,
      
      // Column 1: Number - Changed to Inter
      if num != none {
        text(font: ("Inter", "Helvetica"), size: 0.86em, weight: "bold")[#num]
      } else { [] },
      
      // Column 2: Body
      text(font: ("Inter", "Helvetica"), weight: "medium", el.body),
      
      // Column 3: Page Number - Changed to Inter
      align(right, text(font: ("Inter", "Helvetica"), size: 0.86em)[#str(page_num)])
    )
    v(0.5em)
  }

  // 7. Content Styles
  
  // Lists: Swiss style uses dashes
  set list(marker: [--])
  
  // Quotes: Line on the left, italic text
  show quote: q => {
    block(stroke: (left: 1pt + black), inset: (left: 1em, y: 0.5em))[
      #text(style: "italic", q.body)
    ]
  }

  // Block Equations: Indented
  show math.equation.where(block: true): it => {
    pad(left: 2em, it)
  }

  show figure: it => {
    // Check if this is a table figure using kind
    let is_table = it.kind == table or it.body.func() == table

    if is_table {
      // Table: Margin LINE aligned with Table TOP BORDER
      v(2.5em)
      place(dx: -5.5cm, box(width: 5cm, align(left)[
        #line(length: 100%, stroke: 1pt)
        #v(0.5em)
        #set text(font: ("Inter", "Helvetica"), size: 0.76em, fill: luma(100))
        #set par(leading: 0.5em)
        #if it.numbering != none {
          strong(it.supplement)
          " "
          strong(it.counter.display(it.numbering))
          [: ]
        }
        #it.caption
      ]))
      
      // Table body: NO LINE above it
      block(width: 100%)[
        #it.body
      ]
      v(2.5em)
    } else {
      // Regular figures: keep original style
      v(2.5em)
      place(dx: -5.5cm, box(width: 5cm, align(left)[
        #line(length: 100%, stroke: 1pt)
        #v(0.5em)
        #set text(font: ("Inter", "Helvetica"), size: 0.76em, fill: luma(100))
        #set par(leading: 0.5em)
        #if it.numbering != none {
          strong(it.supplement)
          " "
          strong(it.counter.display(it.numbering))
          [: ]
        }
        #it.caption
      ]))
      
      block(width: 100%)[
        #line(length: 100%, stroke: 1pt)
        #v(0.5em)
        #it.body
      ]
      v(2.5em)
    }
  }

  // Table base styling (must come before show rules)
  set table(
    stroke: 1pt + black,
    align: left + horizon,
    inset: 0.5em,
    fill: (x, y) => if y == 0 { black } else { white }
  )
  
  // General table text styling
  show table: it => {
    set text(size: 8pt, font: ("Inter", "Helvetica"))
    it
  }
  
  // Style header row text
  show table.cell.where(y: 0): it => {
    set text(fill: white, weight: 700)  // Numeric weight works better with variable fonts
    it
  }


  // --- GLOBAL PAGE SETUP (Applies to Page 0 onwards) ---
  // Start counting from 0
  counter(page).update(0)

  set page(
    paper: "a4",
    margin: (top: 4cm, bottom: 4cm, left: 7cm, right: 3cm),
    header: context {
      set text(font: ("Space Mono", "Courier New"), size: 8pt)
      pad(left: -5.5cm, grid(
        columns: (5cm, 0.5cm, 1fr),
        align(bottom, [ // Align elements to bottom of cell
          // Page number (margin)
          #counter(page).display() // Dynamic page number
          #v(0.5em) // Matches body padding
          #line(length: 100%, stroke: 1pt)
        ]),
        [], // Gap
        align(bottom, [ // Align elements to bottom of cell
          // Title (body)
          #upper(title)
          #v(0.5em) // Matches margin padding
          #line(length: 100%, stroke: 1pt)
        ])
      ))
    },
    
    footer: context {
      set text(font: ("Space Mono", "Courier New"), size: 8pt, fill: luma(120))
      pad(left: -5.5cm, grid(
        columns: (5cm, 0.5cm, 1fr),
        align(top + left, [
          #line(length: 100%, stroke: 1pt)
          #v(0.5em)  // Matches header padding
          #if date != none { date } else { "2025" }
        ]),
        [],
        align(top + left, [
          #line(length: 100%, stroke: 1pt)
          #v(0.5em)  // Matches header padding
          #authors.join(", ") 
        ])
      ))
    }
  )

  // --- PHASE 1: TITLE PAGE (Page 0) ---
  
  // Title & Metadata Block (using pad+grid)
  // Note: The header is now ACTIVE here, providing the top lines.
  v(0.5cm)
  pad(left: -5.5cm, grid(
    columns: (5cm, 0.5cm, 1fr),
    
    // Metadata (with line)
    align(top + left)[
      #set text(font: ("Space Mono", "Courier New"), size: 0.76em) 
      #strong("AUTHORS") \
      #v(0.2em)
      #authors.join("\n")
      #v(1em)
      #strong("DATE") \
      #v(0.2em)
      #if date != none { date } else { "-" }
    ],
    [],
    // Title
    align(top + left)[
      #par(leading: 0.9em)[
        #text(size: 4em, weight: "black", title)
      ]
      #if subtitle != none {
        v(0.8em)
        par(leading: 0.6em)[
          #text(size: 1.48em, weight: "regular", fill: luma(100), subtitle)
        ]
      }
    ]
  ))

  v(5em) 

  if abstract != none {
    block(width: 100%)[
      #text(font: ("Space Mono", "Courier New"), size: 0.86em, weight: "bold", "ABSTRACT")
      #v(0.5em)
      #abstract
    ]
    v(3em) 
  }
  
  // --- PHASE 2: MAIN CONTENT (Page 1+) ---
  pagebreak()
  // No need to reset page counter or redefine set page here anymore

  body
}