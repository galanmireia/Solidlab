package DIP;

public class Impact {

	public double calculateImpact(double years, double afection, double increment) {
		// TODO Auto-generated method stub
		double impact=0;
		if (years < 10) {
			impact = 0;
		}else {
			impact = afection + increment;
		}			
		return impact;
	}

}
