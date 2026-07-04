/// <reference types="vite/client" />

declare module 'pdfmake/build/pdfmake' {
  const pdfMake: any
  export default pdfMake
}
declare module 'pdfmake/build/vfs_fonts' {
  const pdfFonts: any
  export default pdfFonts
}
declare module 'html-to-pdfmake' {
  const htmlToPdfmake: (html: string, options?: Record<string, unknown>) => any
  export default htmlToPdfmake
}
