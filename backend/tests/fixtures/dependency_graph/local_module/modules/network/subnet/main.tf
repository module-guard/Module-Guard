variable "cidr" {}
resource "aws_subnet" "private" {
  cidr_block = var.cidr
}
output "id" {
  value = aws_subnet.private.id
}
