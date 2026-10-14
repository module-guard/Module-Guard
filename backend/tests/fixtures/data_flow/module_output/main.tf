module "compute" { source = "./modules/compute" }
resource "aws_eip" "ip" { instance = module.compute.instance_id }
output "alias" { value = module.compute.instance_id }
