export function PageHeading({
  title,
  description,
}: Readonly<{ title: string; description: string }>) {
  return (
    <header className="page-heading">
      <h1>{title}</h1>
      <p>{description}</p>
    </header>
  );
}
