// Export PDF partagé — génère un PDF avec une VRAIE couche texte (sélectionnable,
// extractible par les agents NLP comme la CSD) à partir de HTML.
//
// html2pdf.js (html2canvas) rasterisait tout en JPEG → aucune extraction possible :
// les rapports sortis du chat étaient illisibles pour les agents. pdfmake +
// html-to-pdfmake convertissent le HTML (titres, paragraphes, tableaux) en
// définition de document pdfmake puis génèrent le PDF côté client sans canvas.
//
// Unique point d'export pour les deux chemins (widget éditeur + carte rapport du
// chat) afin que les deux produisent exactement le même PDF extractible.
export async function exportReportPdf(html: string, filename = 'rapport-hemicycle.pdf') {
  const pdfMake = (await import('pdfmake/build/pdfmake')).default
  const pdfFonts = (await import('pdfmake/build/vfs_fonts')).default
  const htmlToPdfmake = (await import('html-to-pdfmake')).default
  // @ts-ignore — pdfmake charge ses polices via vfs à l'initialisation
  pdfMake.vfs = pdfFonts.vfs

  const content = htmlToPdfmake(html, { tableAutoSize: true })

  const docDefinition = {
    pageSize: 'A4' as const,
    pageMargins: [56, 56, 56, 56] as [number, number, number, number],
    content,
    defaultStyle: { font: 'Roboto', fontSize: 11, lineHeight: 1.4 },
    styles: {
      h1: { fontSize: 20, bold: true, marginBottom: 8, color: '#223061' },
      h2: { fontSize: 16, bold: true, marginBottom: 6, color: '#223061' },
      h3: { fontSize: 13, bold: true, marginBottom: 4, color: '#223061' },
    },
  }
  pdfMake.createPdf(docDefinition).download(filename)
}
