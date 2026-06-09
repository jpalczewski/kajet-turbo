declare global {
  namespace App {
    interface PageData {
      session: { email: string } | null
    }
  }
}

export {}
