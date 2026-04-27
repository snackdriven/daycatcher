import { Link } from "wouter";

export default function NotFound() {
  return (
    <div className="wrap py-32 text-center">
      <span className="hand text-2xl text-muted-foreground">oh.</span>
      <h1 className="font-serif text-5xl mt-2">that page is not here.</h1>
      <p className="mt-4 text-foreground/80">
        the suit doesn't keep score. it also doesn't keep that page.
      </p>
      <Link
        href="/"
        className="inline-block mt-8 text-primary underline underline-offset-4"
      >
        back home
      </Link>
    </div>
  );
}
